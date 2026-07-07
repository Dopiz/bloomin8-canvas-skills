# Countdown / Day Counter

Renders a countdown screen for a user-provided date — big serif day count, event title, and date over a public-domain painting — and pushes it to the Canvas. Backgrounds come from the Met Museum's public API (no key needed); the artwork is picked deterministically from the day count, so it rotates daily but stays stable within a day. Past dates flip the label to "days since", so it doubles as an anniversary counter.

`SKILL_DIR` below is this skill's base directory (shown when the skill loads).

## Standard flow

```bash
CLIENT="$SKILL_DIR/scripts/client.py"

# 1. Render (fetch background artwork -> HTML -> screenshot -> JPEG)
uv run "$SKILL_DIR/scripts/countdown.py" --date 2026-12-31 --title "New Year's Eve" --out /tmp/countdown.jpg
# prints JSON: {"out": ..., "days": 177, "background": "<artwork credit>", ...}

# 2. Upload + display (wakes the frame if asleep, uploads as countdown_<timestamp>.jpg, waits for Ready, verifies the display)
FN=$(uv run "$CLIENT" upload /tmp/countdown.jpg --prefix countdown | jq -r .filename)

# 3. Clean up: older countdown images on-device, and the local JPEG
uv run "$CLIENT" cleanup --prefix countdown_ --keep "$FN"
rm -f /tmp/countdown.jpg
```

For a daily-refreshing countdown, schedule the three steps via cron (see `references/crypto-dashboard.md` for the cron pattern — same notes about env vars and BLE permissions apply).

## countdown.py options

| Flag | Default | Notes |
|------|---------|-------|
| `--date` | (required) | Target date, `YYYY-MM-DD`. Future dates show "days until"; past dates show "days since" (anniversary mode). |
| `--title` | `The Big Day` | Event name, e.g. `"Our Wedding Day"`. |
| `--bg` | — | Use a local photo as the background instead of fetched artwork (e.g. an engagement photo). JPEG or PNG. |
| `--bg-query` | `van gogh landscape` | Artwork search theme. Matched against artist names first (`monet`, `van gogh`), falling back to full-text (`garden flowers`). |
| `--width` / `--height` | auto | Auto-detected from `/deviceInfo`; falls back to 1200×1600 if the device is asleep. |
| `--orientation` | `portrait` | `landscape` renders sideways then rotates the bitmap back to the panel's native portrait dimensions. |
| `--rotate` | `cw` | Landscape only. Use `ccw` if the image appears upside-down on your mounting. |
| `--out` | `countdown.jpg` | Output JPEG path. |

## Background design notes

- Backgrounds are public-domain paintings from the Met Museum API, downloaded at web-large size and cover-cropped by CSS; oil-painting color blocks dither beautifully on the Spectra 6 panel. Each fetched artwork is cached in `~/.cache/bloomin8-art-bg/` (image + credit), and offline runs fall back to the cache.
- The artwork index is `abs(days) % result count`, so a daily cron refresh naturally rotates through paintings while staying deterministic within a day.
- Text sits on a bottom gradient scrim (transparent → 58% black) for legibility on any painting; the artwork credit renders bottom-left, truncated with an ellipsis if long.
- Typography is system serif (Didot on macOS-rendered screenshots) to match the elegant look of the official app's countdown widget.

## Troubleshooting

- **`artwork API failed and no cached backgrounds available`** → the Met API is unreachable and the cache is empty; retry when online, or pass `--bg` with a local image.
- **The background painting doesn't fit the event's mood** → change `--bg-query` (e.g. `monet`, `impressionism garden`, `japanese woodblock`), or pin a specific image with `--bg`.
- **Day count seems off by one** → the count is calendar-day based (`target - today`), timezone taken from the local machine; the day of the event itself shows `0 days until`.
- Upload/display issues → see the main SKILL.md gotchas (fresh filenames, wake, Ready polling are all handled by `client.py`).
