# Today's Weather Dashboard

Renders a today's-weather screen as a JPEG sized to the frame's resolution and pushes it to the Canvas. The top of the screen shows the current temperature (extra large), condition text, and rain probability; the bottom shows a 5-slot outlook every 3 hours anchored at the current hour (e.g. at 16:00 the slots are Now, 7 PM, 10 PM, 1 AM, 4 AM). Data comes from Open-Meteo's free forecast API (no key needed); the render pipeline is: fetch forecast → e-ink-optimized HTML template → headless Chromium screenshot → JPEG.

`SKILL_DIR` below is this skill's base directory (shown when the skill loads).

## One-time setup

```bash
uv run --with playwright playwright install chromium
```

## Standard flow

```bash
CLIENT="$SKILL_DIR/scripts/client.py"

# 1. Render (fetch forecast -> HTML -> screenshot -> JPEG)
uv run "$SKILL_DIR/scripts/weather.py" --out /tmp/weather.jpg
# prints JSON: {"out": ..., "size": [1200, 1600], "temp": 31, "condition": "Partly Cloudy", ...}

# 2. Upload + display (wakes the frame if asleep, uploads as weather_<timestamp>.jpg, waits for Ready, verifies the display)
FN=$(uv run "$CLIENT" upload /tmp/weather.jpg --prefix weather | jq -r .filename)

# 3. Clean up: older dashboards on-device, and the local JPEG (the frame keeps its own copy once uploaded)
uv run "$CLIENT" cleanup --prefix weather_ --keep "$FN"
rm -f /tmp/weather.jpg
```

## weather.py options

| Flag | Default | Notes |
|------|---------|-------|
| `--lat` / `--lon` | `$BLOOMIN8_LAT` / `$BLOOMIN8_LON`, else Taipei (25.033, 121.565) | Location for the forecast query. |
| `--city` | `Taipei` | Display label only — it is not geocoded, so keep it consistent with `--lat`/`--lon`. |
| `--orientation` | `portrait` | `landscape` renders sideways then rotates the bitmap back to the panel's native portrait dimensions; the hero and forecast column sit side by side. |
| `--rotate` | `cw` | Landscape only. If the image appears upside-down on your mounting, use `ccw`. |
| `--width` / `--height` | auto | Auto-detected from `/deviceInfo`; falls back to 1200×1600 if the device is asleep. Pass explicitly to skip the probe. |
| `--out` | `weather.jpg` | Output JPEG path. |

## Template design notes

`assets/weather-template.html` is tuned for a Spectra 6 color e-ink panel:

- Full-bleed gradient card with white text and white line-art icons, column layout (date, time, city, hero icon, huge thin temperature, condition · rain %, divider, forecast strip) spaced with `justify-content: space-between` for breathing room top and bottom. The gradient theme is driven by the CURRENT WEATHER CONDITION, not the clock (`pick_theme(icon)` in `weather.py`, mapped via `ICON_THEME`): clear → warm amber-gold, partly/overcast → cool slate blue-gray, fog → muted gray-teal, drizzle/rain → steel blue, thunderstorm → dark dramatic purple-navy, snow → pale icy blue. On the Spectra 6 panel the saturated gradients dither into a soft painterly texture while the white strokes stay crisp. A `--force-icon` debug flag overrides the icon/condition/theme for previewing any of the 8 groups without waiting for matching live weather.
- Portrait height is taller than 100 "vmin" units (vmin is based on the shorter dimension — width — so a 1200×1600 panel has ~133vmin of height), which gives some slack but not much: forecast cell time labels are forced `white-space: nowrap` deliberately, since a wrapped label (e.g. "12 AM") used to add just enough height to push the forecast row past the bottom edge, silently clipped by `overflow: hidden`. Keep new labels/content short or verify against real device dimensions if you extend this template.
- Weather icons are hand-drawn inline SVG symbols in a thin white outline style (stroke only, no fills, no emoji), grouped from WMO weather codes into 8 buckets: clear, partly cloudy, overcast, fog, drizzle, rain, snow, thunderstorm. The mapping lives in the `WMO` dict in `weather.py`; unknown codes fall back to the overcast cloud.
- All sizes use `vmin`, so portrait (1200×1600) and landscape (1600×1200) share one template; an `@media (orientation: landscape)` block compresses the column so everything fits the shorter height.
- Data source limitations: Open-Meteo's `current` block has no precipitation probability, so the script takes it from the hourly series at the current hour (`timezone=auto` keeps both in local time). Icons do not distinguish day from night — a clear 2 AM slot still shows a sun. If the API is unreachable the script exits non-zero with the error; it never renders stale or fake data.

## Troubleshooting

- **`playwright` errors about missing browser** → run the one-time setup line.
- **`Open-Meteo API failed: ...`** → network or upstream issue; the script exits non-zero by design (no fake data). Retry, or check `curl -s 'https://api.open-meteo.com/v1/forecast?latitude=25&longitude=121&current=temperature_2m'`.
- **Wrong city's weather** → `--city` is only a label; verify `--lat`/`--lon` (or the `BLOOMIN8_LAT`/`BLOOMIN8_LON` env vars) point at the right place.
- **Condition text says Clear but the rain pill shows a high %** → not a bug: the condition reflects the current-hour WMO code while the percentage is the current hour's precipitation probability; they can legitimately disagree.
- **Image looks stretched** → width/height didn't match the panel; check the size in the script's JSON output against `client.py info`.
- **Upload OK but screen unchanged** → device may be mid-refresh; run `client.py wait-ready`, and see the main SKILL.md for wake/sleep gotchas.
