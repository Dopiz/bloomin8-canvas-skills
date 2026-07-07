# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40", "pillow", "requests"]
# ///
"""Render a countdown (or days-since) screen for a Bloomin8 e-ink frame.

Background art comes from the Met Museum's public API (public-domain paintings,
no key needed), picked deterministically by the day count — so the artwork
rotates daily but is stable within a day. Fetched images are cached in
~/.cache/bloomin8-art-bg/ so offline cron runs keep working. Pass --bg to use
your own photo instead.

Typical usage:  uv run countdown.py --date 2026-12-31 --title "New Year's Eve" --out /tmp/countdown.jpg
"""
import argparse
import base64
import json
import sys
from datetime import date
from pathlib import Path

import requests

from eink_render import html_to_jpeg, resolve_render_size

MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
ART_CACHE = Path.home() / ".cache" / "bloomin8-art-bg"


def fetch_background(query: str, pick: int) -> tuple[bytes, str]:
    """Return (image bytes, credit line). Falls back to any cached artwork when
    the API is unreachable, so scheduled runs don't break offline."""
    ART_CACHE.mkdir(parents=True, exist_ok=True)
    try:
        # Prefer matching the query against artist names (e.g. "van gogh",
        # "monet") — full-text matching often surfaces busy altarpieces and
        # studies that make poor text backgrounds. Theme queries that match no
        # artist (e.g. "garden flowers") fall back to full-text search.
        ids: list = []
        for artist_only in ("true", "false"):
            r = requests.get(
                MET_SEARCH,
                params={"q": query, "hasImages": "true", "medium": "Paintings",
                        "artistOrCulture": artist_only},
                timeout=15,
            )
            r.raise_for_status()
            ids = (r.json().get("objectIDs") or [])[:60]
            if ids:
                break
        if not ids:
            raise RuntimeError(f"no artwork found for '{query}'")
        # Deterministic pick by day count; some objects lack a usable image or
        # aren't public domain, so walk forward until one qualifies.
        for offset in range(min(len(ids), 12)):
            oid = ids[(pick + offset) % len(ids)]
            cached = ART_CACHE / f"met_{oid}.jpg"
            if cached.exists():
                meta = {}
            else:
                meta = requests.get(MET_OBJECT.format(oid), timeout=15).json()
                if not (meta.get("isPublicDomain") and meta.get("primaryImageSmall")):
                    continue
                img = requests.get(meta["primaryImageSmall"], timeout=30)
                img.raise_for_status()
                cached.write_bytes(img.content)
                credit_file = cached.with_suffix(".txt")
                credit_file.write_text(" — ".join(
                    x for x in (meta.get("title"), meta.get("artistDisplayName")) if x
                ))
            credit_file = cached.with_suffix(".txt")
            credit = credit_file.read_text() if credit_file.exists() else ""
            return cached.read_bytes(), credit
        raise RuntimeError(f"no public-domain image among results for '{query}'")
    except Exception as e:
        fallback = sorted(ART_CACHE.glob("*.jpg"))
        if fallback:
            print(f"warn: artwork API failed ({e}); using a cached background", file=sys.stderr)
            return fallback[pick % len(fallback)].read_bytes(), ""
        raise SystemExit(f"artwork API failed and no cached backgrounds available: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="target date, YYYY-MM-DD")
    ap.add_argument("--title", default="The Big Day", help='event name, e.g. "Our Wedding Day"')
    ap.add_argument("--bg", help="use this local image as the background instead of fetched artwork")
    ap.add_argument("--bg-query", default="van gogh landscape",
                    help="artwork search theme, e.g. 'monet garden', 'impressionism flowers'")
    ap.add_argument("--width", type=int, help="device native width (default: /deviceInfo or 1200)")
    ap.add_argument("--height", type=int, help="device native height (default: /deviceInfo or 1600)")
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--rotate", default="cw", choices=["cw", "ccw"],
                    help="landscape only: rotation applied to fit the portrait-native panel")
    ap.add_argument("--out", default="countdown.jpg")
    args = ap.parse_args()

    target = date.fromisoformat(args.date)
    days = (target - date.today()).days
    label = "days until" if days >= 0 else "days since"

    if args.bg:
        bg_bytes = Path(args.bg).expanduser().read_bytes()
        mime = "image/png" if args.bg.lower().endswith(".png") else "image/jpeg"
        credit = ""
    else:
        bg_bytes, credit = fetch_background(args.bg_query, abs(days))
        mime = "image/jpeg"
    bg_uri = f"data:{mime};base64,{base64.b64encode(bg_bytes).decode()}"

    template = (Path(__file__).parent.parent / "assets" / "countdown-template.html").read_text()
    html = (
        template.replace("{{BG_URI}}", bg_uri)
        .replace("{{DAYS}}", str(abs(days)))
        .replace("{{LABEL}}", label)
        .replace("{{TITLE}}", args.title)
        .replace("{{DATE}}", target.strftime("%B %-d, %Y"))
        .replace("{{CREDIT}}", credit)
    )

    render_w, render_h = resolve_render_size(args.orientation, args.width, args.height)
    out, size = html_to_jpeg(html, render_w, render_h, args.out,
                             orientation=args.orientation, rotate=args.rotate)

    print(json.dumps({
        "out": str(out),
        "size": list(size),
        "days": days,
        "title": args.title,
        "date": args.date,
        "background": credit or (args.bg or "cached"),
        "orientation": args.orientation,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
