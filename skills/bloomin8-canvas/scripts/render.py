# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40", "pillow", "requests"]
# ///
"""Render a crypto price dashboard as a JPEG sized for a Bloomin8 e-ink frame.

Pipeline: Binance public API -> HTML template (inline SVG sparkline)
          -> headless Chromium screenshot -> JPEG (rotated for landscape).

One-time setup:  uv run --with playwright playwright install chromium
Typical usage:   uv run render.py --out /tmp/crypto.jpg
"""
import argparse
import base64
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

from eink_render import html_to_jpeg, resolve_render_size

BINANCE_HOSTS = ["api.binance.com", "api1.binance.com", "api2.binance.com"]
RANGES = {  # range -> (kline interval, number of klines)
    "24h": ("1h", 24),
    "7d": ("4h", 42),
    "30d": ("1d", 30),
}
QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "TUSD")
UP_COLOR, DOWN_COLOR = "#009e3c", "#e11900"
ICON_CDN = "https://raw.githubusercontent.com/spothq/cryptocurrency-icons/master/128/color/{}.png"
ICON_CACHE = Path.home() / ".cache" / "bloomin8-crypto-icons"


def binance_get(path: str, params: dict) -> object:
    last_err = None
    for host in BINANCE_HOSTS:
        try:
            r = requests.get(f"https://{host}{path}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # try next mirror
            last_err = e
    raise SystemExit(f"Binance API failed on all hosts ({path}): {last_err}")


def normalize_symbol(sym: str) -> str:
    sym = sym.strip().upper()
    return sym if sym.endswith(QUOTE_SUFFIXES) else sym + "USDT"


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 1:
        return f"{p:,.2f}"
    return f"{p:.4f}"


def sparkline_chart(closes: list[float], baseline: float, accent: str, w: int = 560, h: int = 160) -> str:
    """Sparkline with a dashed reference line at `baseline` (the range's opening
    price, i.e. the 0%-change level). The SVG stretches to fill the chart area
    (preserveAspectRatio="none"), which would distort any shape drawn inside
    it — so strokes use vector-effect: non-scaling-stroke, and the endpoint dot
    is an HTML overlay positioned in percentages, keeping it perfectly round."""
    lo = min(min(closes), baseline)
    hi = max(max(closes), baseline)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(closes)
    pts = [
        (pad + i * (w - 2 * pad) / (n - 1), pad + (hi - c) * (h - 2 * pad) / span)
        for i, c in enumerate(closes)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad:.1f},{h - pad:.1f} {line} {w - pad:.1f},{h - pad:.1f}"
    by = pad + (hi - baseline) * (h - 2 * pad) / span
    lx, ly = pts[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polygon points="{area}" fill="#e0e0e0"/>'
        f'<line x1="{pad}" y1="{by:.1f}" x2="{w - pad}" y2="{by:.1f}" stroke="#000" '
        f'stroke-width="2" stroke-dasharray="8 8" vector-effect="non-scaling-stroke"/>'
        f'<polyline points="{line}" fill="none" stroke="#000" stroke-width="3.5" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        f"</svg>"
        f'<span class="dot" style="left:{lx / w * 100:.2f}%;top:{ly / h * 100:.2f}%;'
        f'background:{accent}"></span>'
    )


def icon_html(base: str) -> str:
    """Coin icon as an embedded data URI (cached locally after first fetch);
    falls back to a black circle with the ticker's initial when unavailable,
    so offline cron runs never break on a missing icon."""
    ICON_CACHE.mkdir(parents=True, exist_ok=True)
    cached = ICON_CACHE / f"{base.lower()}.png"
    if not cached.exists():
        try:
            r = requests.get(ICON_CDN.format(base.lower()), timeout=10)
            r.raise_for_status()
            cached.write_bytes(r.content)
        except Exception:
            return f'<div class="icon fallback">{base[0]}</div>'
    uri = "data:image/png;base64," + base64.b64encode(cached.read_bytes()).decode()
    return f'<img class="icon" src="{uri}">'


def build_card(symbol: str, rng: str) -> str:
    interval, limit = RANGES[rng]
    ticker = binance_get("/api/v3/ticker/24hr", {"symbol": symbol})
    klines = binance_get(
        "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}
    )
    closes = [float(k[4]) for k in klines]
    range_open = float(klines[0][1])
    price = float(ticker["lastPrice"])
    change = float(ticker["priceChangePercent"])
    up = change >= 0
    accent = UP_COLOR if up else DOWN_COLOR
    base = re.sub(f"({'|'.join(QUOTE_SUFFIXES)})$", "", symbol)
    quote = symbol[len(base):]
    return f"""    <div class="card"><div class="card-inner">
      <div class="card-head">
        <div class="symbol-wrap">{icon_html(base)}<div class="symbol">{base} <small>/ {quote}</small></div></div>
        <div class="badge {'up' if up else 'down'}">{'▲' if up else '▼'} {abs(change):.2f}%<span class="tf">24H</span></div>
      </div>
      <div class="price"><span class="cur">$</span>{fmt_price(price)}</div>
      <div class="chart">{sparkline_chart(closes, range_open, accent)}</div>
      <div class="range-row"><span>L ${fmt_price(min(closes))}</span><span>H ${fmt_price(max(closes))}</span></div>
    </div></div>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="BTC,ETH", help="comma-separated, e.g. BTC,ETH,SOL")
    ap.add_argument("--range", default="24h", choices=RANGES, dest="rng")
    ap.add_argument("--width", type=int, help="device native width (default: /deviceInfo or 1200)")
    ap.add_argument("--height", type=int, help="device native height (default: /deviceInfo or 1600)")
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--rotate", default="cw", choices=["cw", "ccw"],
                    help="landscape only: rotation applied to fit the portrait-native panel")
    ap.add_argument("--out", default="crypto_dashboard.jpg")
    args = ap.parse_args()

    render_w, render_h = resolve_render_size(args.orientation, args.width, args.height)

    symbols = [normalize_symbol(s) for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("no symbols given")

    cards = "\n".join(build_card(s, args.rng) for s in symbols)
    # Grid density: up to 3 cards stack in a single line; beyond that, wrap
    # into 2 rows/columns so cards keep a readable aspect ratio.
    n = len(symbols)
    cols_portrait = 1 if n <= 3 else 2
    cols_landscape = n if n <= 3 else math.ceil(n / 2)
    template = (Path(__file__).parent.parent / "assets" / "crypto-template.html").read_text()
    html = (
        template.replace("{{CARDS}}", cards)
        .replace("{{COLS_PORTRAIT}}", str(cols_portrait))
        .replace("{{COLS_LANDSCAPE}}", str(cols_landscape))
        .replace("{{RANGE_LABEL}}", args.rng.upper())
        .replace("{{UPDATED_AT}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    )

    out, size = html_to_jpeg(html, render_w, render_h, args.out,
                             orientation=args.orientation, rotate=args.rotate)

    print(json.dumps({
        "out": str(out),
        "size": list(size),
        "symbols": symbols,
        "range": args.rng,
        "orientation": args.orientation,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
