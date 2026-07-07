"""Shared rendering helpers for Bloomin8 dashboard scripts.

The panel is portrait-native (width < height). Landscape renders with swapped
dimensions, then the bitmap is rotated back to native orientation before saving.
Imported by the dashboard entry scripts (render.py, countdown.py, ...); each of
those declares the runtime dependencies (playwright, pillow, requests) in its
own PEP 723 header.
"""
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_W, DEFAULT_H = 1200, 1600


def device_dimensions() -> tuple[int, int]:
    import requests

    ip = os.environ.get("BLOOMIN8_LAN_IP")
    if ip:
        try:
            info = requests.get(f"http://{ip}/deviceInfo", timeout=3).json()
            return int(info["width"]), int(info["height"])
        except Exception:
            print(
                f"warn: /deviceInfo unreachable, falling back to {DEFAULT_W}x{DEFAULT_H}",
                file=sys.stderr,
            )
    return DEFAULT_W, DEFAULT_H


def resolve_render_size(orientation: str, width: int | None = None, height: int | None = None) -> tuple[int, int]:
    dev_w, dev_h = (width, height) if width and height else device_dimensions()
    if orientation == "landscape":
        return max(dev_w, dev_h), min(dev_w, dev_h)
    return min(dev_w, dev_h), max(dev_w, dev_h)


def html_to_jpeg(html: str, render_w: int, render_h: int, out: str,
                 orientation: str = "portrait", rotate: str = "cw") -> tuple[Path, tuple[int, int]]:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        html_path = f.name
    png_path = html_path + ".png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": render_w, "height": render_h}, device_scale_factor=1
        )
        page.goto(f"file://{html_path}")
        page.screenshot(path=png_path)
        browser.close()

    img = Image.open(png_path).convert("RGB")
    if orientation == "landscape":
        img = img.transpose(Image.ROTATE_270 if rotate == "cw" else Image.ROTATE_90)
    out_path = Path(out).expanduser().resolve()
    img.save(out_path, "JPEG", quality=92)
    os.unlink(html_path)
    os.unlink(png_path)
    return out_path, img.size
