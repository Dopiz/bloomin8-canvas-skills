# Crypto Price Dashboard

Renders a BTC/ETH (or any Binance symbol) price + trend dashboard as a JPEG sized to the frame's resolution and pushes it to the Canvas. Data comes from Binance's public API (no key needed); the render pipeline is: fetch prices + klines → e-ink-optimized HTML template → headless Chromium screenshot → JPEG.

`SKILL_DIR` below is this skill's base directory (shown when the skill loads).

## One-time setup

```bash
uv run --with playwright playwright install chromium
```

## Standard flow

```bash
CLIENT="$SKILL_DIR/scripts/client.py"

# 1. Render (fetch prices -> HTML -> screenshot -> JPEG)
uv run "$SKILL_DIR/scripts/render.py" --out /tmp/crypto_dashboard.jpg
# prints JSON: {"out": ..., "size": [1200, 1600], "symbols": [...], ...}

# 2. Upload + display (wakes the frame if asleep, uploads as crypto_<timestamp>.jpg, waits for Ready, verifies the display)
FN=$(uv run "$CLIENT" upload /tmp/crypto_dashboard.jpg --prefix crypto | jq -r .filename)

# 3. Clean up: older dashboards on-device, and the local JPEG (the frame keeps its own copy once uploaded)
uv run "$CLIENT" cleanup --prefix crypto_ --keep "$FN"
rm -f /tmp/crypto_dashboard.jpg
```

## render.py options

| Flag | Default | Notes |
|------|---------|-------|
| `--symbols` | `BTC,ETH` | Comma-separated. Bare tickers get `USDT` appended; full pairs like `SOLUSDC` pass through. Tested 1–6: up to 3 stack in one line, 4+ wrap into a 2-column (portrait) / 2-row (landscape) grid. |
| `--range` | `24h` | `24h` (1h klines) / `7d` (4h) / `30d` (1d). Chart + L/H reflect this range; the % badge is always 24H. |
| `--orientation` | `portrait` | `landscape` renders sideways then rotates the bitmap back to the panel's native portrait dimensions. |
| `--rotate` | `cw` | Landscape only. If the image appears upside-down on your mounting, use `ccw`. |
| `--width` / `--height` | auto | Auto-detected from `/deviceInfo`; falls back to 1200×1600 if the device is asleep. Pass explicitly to skip the probe. |
| `--out` | `crypto_dashboard.jpg` | Output JPEG path. |

## Template design notes

`assets/crypto-template.html` is tuned for a Spectra 6 color e-ink panel:

- Pure black on white; the only colors are saturated green `#009e3c` (up) and red `#e11900` (down) — mid-tones dither into noise on e-ink, so avoid adding grays or pastels if you customize it.
- Grid columns are computed by `render.py` from the symbol count and injected into the template; each card is a CSS size container, so typography scales with the card (`cqmin`, clamped by `cqw` so long prices and badges never overflow narrow cards in dense grids).
- Sparklines include a dashed reference line at the range's opening price (the 0%-change level). The endpoint dot is an HTML overlay so it stays a true circle regardless of how the stretched SVG scales.
- Coin icons come from the `spothq/cryptocurrency-icons` static CDN, cached in `~/.cache/bloomin8-crypto-icons/` and embedded as data URIs — offline runs reuse the cache, and unknown coins fall back to a black initial circle.
- Up/down colors follow the international crypto convention (green = up). For the Taiwan stock convention, swap `UP_COLOR`/`DOWN_COLOR` in `render.py` and `.badge.up`/`.badge.down` in the template.

## Scheduled refresh via cron

cron runs without your shell profile — use absolute paths and export env vars inline. Example: refresh every 30 min during waking hours:

```cron
*/30 8-23 * * * export BLOOMIN8_LAN_IP=192.168.0.87; /opt/homebrew/bin/claude -p "用 bloomin8-canvas skill 刷新畫框上的 BTC/ETH 幣價儀表板" --allowedTools "Bash Skill" >> /tmp/crypto_frame.log 2>&1
```

Notes:
- BLE wake needs Bluetooth permission for whatever binary cron runs under; if wake fails in cron, either grant it or lengthen the frame's `max_idle` / disable sleep via `settings`.
- Token-free alternative: cron the render + client calls directly (no `claude -p`) — same steps 1–3 above in a shell script, locating the skill dir via `find ~/.claude/skills -name render.py -path '*bloomin8*'`. Prefer this if the schedule is fixed and needs no judgment.

## Troubleshooting

- **`playwright` errors about missing browser** → run the one-time setup line.
- **Binance API unreachable** → the script already tries 3 mirror hosts; if all fail it exits non-zero with the error (no stale/fake data is rendered).
- **Image looks stretched** → width/height didn't match the panel; check the size in the script's JSON output against `client.py info`.
- **Upload OK but screen shows the old dashboard** → a filename that was used before is being served from the device's cache. `client.py upload` already prevents this (fresh timestamped filename + display verification); if uploading manually, never reuse a filename and confirm with `/deviceInfo`'s `image` field.
- **Upload OK but screen unchanged** → device may be mid-refresh; run `client.py wait-ready`, and see the main SKILL.md for wake/sleep gotchas.
