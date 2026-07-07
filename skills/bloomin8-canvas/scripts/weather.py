# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40", "pillow", "requests"]
# ///
"""Render a today's-weather dashboard for a Bloomin8 e-ink frame.

Data comes from Open-Meteo's free forecast API (no key needed). The screen
shows the current temperature, condition and rain probability up top, plus a
5-slot outlook (every 3 hours, anchored at the current hour) along the bottom.

Typical usage:  uv run weather.py --lat 25.033 --lon 121.565 --city Taipei --out /tmp/weather.jpg
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

from eink_render import html_to_jpeg, resolve_render_size

API_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> (display label, SVG icon group in the template)
WMO = {
    0: ("Clear Sky", "clear"),
    1: ("Mainly Clear", "clear"),
    2: ("Partly Cloudy", "partly"),
    3: ("Overcast", "overcast"),
    45: ("Fog", "fog"),
    48: ("Rime Fog", "fog"),
    51: ("Light Drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy Drizzle", "drizzle"),
    56: ("Freezing Drizzle", "drizzle"),
    57: ("Freezing Drizzle", "drizzle"),
    61: ("Light Rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy Rain", "rain"),
    66: ("Freezing Rain", "rain"),
    67: ("Freezing Rain", "rain"),
    71: ("Light Snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy Snow", "snow"),
    77: ("Snow Grains", "snow"),
    80: ("Light Showers", "rain"),
    81: ("Rain Showers", "rain"),
    82: ("Heavy Showers", "rain"),
    85: ("Snow Showers", "snow"),
    86: ("Snow Showers", "snow"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunderstorm", "thunder"),
    99: ("Thunderstorm", "thunder"),
}


def wmo_lookup(code) -> tuple[str, str]:
    return WMO.get(int(code), ("Unknown", "overcast"))


def hour_label(iso_time: str) -> str:
    h = datetime.fromisoformat(iso_time).hour
    suffix = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12} {suffix}"


#: icon group (from WMO lookup) -> background gradient theme. The theme is
#: driven by the weather condition itself, not the clock — a thunderstorm at
#: noon should still read as dark and dramatic, and clear skies at night
#: still read as "clear", not stormy.
ICON_THEME = {
    "clear": "clear",
    "partly": "cloudy",
    "overcast": "cloudy",
    "fog": "fog",
    "drizzle": "rain",
    "rain": "rain",
    "snow": "snow",
    "thunder": "thunder",
}


def pick_theme(icon: str) -> str:
    """Gradient theme keyed off the weather condition group (see ICON_THEME)."""
    return ICON_THEME.get(icon, "cloudy")


def fetch_forecast(lat: float, lon: float) -> dict:
    try:
        r = requests.get(
            API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if "current" not in data or "hourly" not in data:
            raise RuntimeError(f"unexpected API response: {list(data)}")
        return data
    except Exception as e:
        raise SystemExit(f"Open-Meteo API failed: {e}")


def build_forecast_cells(hourly: dict, now_idx: int) -> str:
    """Five slots anchored at the CURRENT hour: now, +3h, +6h, +9h, +12h.
    Starting at now (labelled "Now") keeps the strip aligned with what the
    viewer is experiencing when they glance at the frame."""
    cells = []
    times = hourly["time"]
    for step in range(5):
        i = now_idx + step * 3
        if i >= len(times):
            raise SystemExit(f"hourly forecast too short for slot +{step * 3}h")
        _, icon = wmo_lookup(hourly["weather_code"][i])
        pop = hourly["precipitation_probability"][i]
        pop_txt = f"{round(pop)}%" if pop is not None else "–"
        label = "Now" if step == 0 else hour_label(times[i])
        cells.append(
            f'<div class="cell">'
            f'<div class="cell-time">{label}</div>'
            f'<svg class="cell-icon" viewBox="0 0 100 100"><use href="#i-{icon}"/></svg>'
            f'<div class="cell-temp">{round(hourly["temperature_2m"][i])}&deg;</div>'
            f'<div class="cell-pop">{pop_txt}</div>'
            f"</div>"
        )
    return "\n".join(cells)


def main() -> None:
    env_lat, env_lon = os.environ.get("BLOOMIN8_LAT"), os.environ.get("BLOOMIN8_LON")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lat", type=float, default=float(env_lat) if env_lat else 25.033,
                    help="latitude (default: $BLOOMIN8_LAT or Taipei)")
    ap.add_argument("--lon", type=float, default=float(env_lon) if env_lon else 121.565,
                    help="longitude (default: $BLOOMIN8_LON or Taipei)")
    ap.add_argument("--city", default="Taipei", help="display label for the location")
    ap.add_argument("--width", type=int, help="device native width (default: /deviceInfo or 1200)")
    ap.add_argument("--height", type=int, help="device native height (default: /deviceInfo or 1600)")
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--rotate", default="cw", choices=["cw", "ccw"],
                    help="landscape only: rotation applied to fit the portrait-native panel")
    ap.add_argument("--out", default="weather.jpg")
    ap.add_argument("--force-icon", choices=sorted(ICON_THEME), default=None,
                    help="debug/preview only: override the condition icon+theme "
                         "(and swap the condition label to match) without needing "
                         "live weather to match — useful for eyeballing every "
                         "background gradient during development")
    args = ap.parse_args()

    data = fetch_forecast(args.lat, args.lon)
    cur, hourly = data["current"], data["hourly"]

    # `current` has no precipitation probability; take it from the matching
    # hourly slot (times are local thanks to timezone=auto).
    hour_key = cur["time"][:13]  # "YYYY-MM-DDTHH"
    try:
        now_idx = next(i for i, t in enumerate(hourly["time"]) if t[:13] == hour_key)
    except StopIteration:
        raise SystemExit(f"current time {cur['time']} not found in hourly forecast")
    pop_now = hourly["precipitation_probability"][now_idx]
    pop_now = round(pop_now) if pop_now is not None else 0

    condition, icon = wmo_lookup(cur["weather_code"])
    if args.force_icon:
        icon = args.force_icon
        condition = icon.capitalize() + " (forced)"
    now_local = datetime.fromisoformat(cur["time"])

    template = (Path(__file__).parent.parent / "assets" / "weather-template.html").read_text()
    html = (
        template.replace("{{CITY}}", args.city)
        .replace("{{DATE}}", now_local.strftime("%A, %B %-d"))
        .replace("{{TIME}}", now_local.strftime("%-I:%M%p").lower())
        .replace("{{THEME}}", pick_theme(icon))
        .replace("{{TEMP}}", str(round(cur["temperature_2m"])))
        .replace("{{CONDITION}}", condition)
        .replace("{{ICON}}", icon)
        .replace("{{PRECIP}}", str(pop_now))
        .replace("{{FEELS}}", str(round(cur["apparent_temperature"])))
        .replace("{{HUMIDITY}}", str(round(cur["relative_humidity_2m"])))
        .replace("{{CELLS}}", build_forecast_cells(hourly, now_idx))
    )

    render_w, render_h = resolve_render_size(args.orientation, args.width, args.height)
    out, size = html_to_jpeg(html, render_w, render_h, args.out,
                             orientation=args.orientation, rotate=args.rotate)

    print(json.dumps({
        "out": str(out),
        "size": list(size),
        "temp": round(cur["temperature_2m"]),
        "condition": condition,
        "precip_prob": pop_now,
        "city": args.city,
        "orientation": args.orientation,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
