---
name: bloomin8-canvas
description: Control a Bloomin8 e-ink Canvas directly over the LAN (no cloud, no auth), and render any information screen onto it. Trigger when the user mentions Bloomin8, Canvas, e-ink frame, or asks to push an image, manage galleries/playlists, sleep/wake/reboot the device, check its status, or show/refresh any content on the frame — e.g. crypto prices (BTC/ETH/幣價), countdown/anniversary screens (倒數日/紀念日), weather (天氣/降雨) — including cron/scheduled refreshes.
allowed-tools: Bash(curl:*), Bash(jq:*), Bash(printenv:*), Bash(test:*), Bash(file:*), Bash(magick:*), Bash(convert:*), Bash(sips:*), Bash(uv:*), Bash(source:*), Bash(find:*)
---

# Bloomin8 Canvas — LAN Direct Control

The Canvas exposes an unauthenticated HTTP API on the local network. **Pushing goes device-to-device — no cloud involvement.** This is the preferred path; the cloud `einkshot.run.app` API is intentionally not used.

**All operations go through `scripts/client.py`** (CLI or Python module). It is the single place where the firmware gotchas are handled — BLE wake when asleep, fresh-filename uploads, Ready polling, display verification — so don't hand-roll curl chains for device operations. The raw HTTP API is documented at the bottom for debugging and for understanding payload shapes.

**Task routing** — read the matching reference before starting:

- Crypto price dashboard (render BTC/ETH/... prices + trends and push to the frame, or a scheduled refresh) → read `references/crypto-dashboard.md`
- Countdown / day counter (days until a wedding, trip, deadline; days since an anniversary) → read `references/countdown.md`
- Weather dashboard (current temperature + rain probability + 15-hour forecast) → read `references/weather.md`
- Everything else (status, push an image, galleries, playlists, power) → this file is all you need

## Setup

- **Base URL**: `http://$BLOOMIN8_LAN_IP` (e.g. `192.168.0.87`) — required env var.
- **No auth required** — pure LAN trust model. Same network segment required (no VLAN isolation between client and Canvas).
- For BLE wake: `BLOOMIN8_BLE_MAC` and optional `BLOOMIN8_BLE_NAME` (default `Bloomin8`).

Env vars must be visible to non-interactive zsh — put them in `~/.zshenv`, not `~/.zshrc`, OR `source ~/.zshrc` at the start of any session that needs them.

All scripts live in this skill's directory. `SKILL_DIR` below is the skill's base directory (shown when the skill loads):

```bash
CLIENT="$SKILL_DIR/scripts/client.py"
```

In contexts without the skill loaded (e.g. a cron shell script), locate it with `find ~/.claude/skills -name client.py -path '*bloomin8*' | head -1`.

## Operations — `client.py`

Every command prints a JSON result and exits non-zero on failure. All commands except `state` / `wait-ready` / `wake` automatically BLE-wake the device first if it is asleep.

### Status / power

```bash
uv run "$CLIENT" info          # /deviceInfo — width, height, battery, current image, gallery, ...
uv run "$CLIENT" state         # /state — {status, msg}; 100 = Ready
uv run "$CLIENT" wait-ready    # poll /state until status 100
uv run "$CLIENT" wake          # BLE-wake if asleep, poll until reachable
uv run "$CLIENT" whistle       # keep-alive; postpones sleep during long sessions
uv run "$CLIENT" sleep
uv run "$CLIENT" reboot
uv run "$CLIENT" clear-screen  # clear the panel to white
uv run "$CLIENT" settings '{"name":"Living Room","sleep_duration":86400,"max_idle":300,"idx_wake_sens":3}'   # any subset of fields
```

### Push content

The image must be a JPEG already sized to the device's `width × height` from `info` (e.g. EL133UF1 = 1200×1600). `upload` generates a fresh `<prefix>_<timestamp>.jpg` filename, waits for Ready, and verifies `/deviceInfo.image` actually points at the new file — never pass content through a fixed filename yourself (see gotchas).

```bash
uv run "$CLIENT" upload /path/to/image.jpg --prefix myapp              # upload + display, returns {"filename": ...}
uv run "$CLIENT" upload /path/to/image.jpg --prefix myapp --no-show    # upload only
uv run "$CLIENT" cleanup --prefix myapp_ --keep myapp_20260707_162811.jpg   # delete older same-prefix images
uv run "$CLIENT" image-delete some_file.jpg --gallery default
```

For best e-ink rendering, pre-dither locally before upload (optional — the device also dithers):

```bash
magick input.png -resize 1200x1600^ -gravity center -extent 1200x1600 -dither FloydSteinberg -colors 64 -quality 90 output.jpg
```

### Display / playback

```bash
uv run "$CLIENT" show-image /gallerys/default/f1.jpg    # display an image already on the device
uv run "$CLIENT" show-gallery default --duration 120    # slideshow, seconds per image
uv run "$CLIENT" show-playlist daily_show
uv run "$CLIENT" show-next                              # skip to next in current queue
```

### Galleries

```bash
uv run "$CLIENT" gallery-list
uv run "$CLIENT" gallery-create vacation
uv run "$CLIENT" gallery-images default --offset 0 --limit 50
uv run "$CLIENT" gallery-delete vacation                # deletes ALL images inside too
```

### Playlists

A playlist is an ordered list of image refs with per-item `duration` (seconds) OR a wall-clock `time` (string); `type` selects which field matters.

```bash
uv run "$CLIENT" playlist-list
uv run "$CLIENT" playlist-get daily_show
uv run "$CLIENT" playlist-set '{"name":"daily_show","type":"duration","list":[{"name":"/gallerys/default/f1.jpg","duration":40,"time":""},{"name":"/gallerys/default/f2.jpg","duration":40,"time":""}]}'
uv run "$CLIENT" playlist-delete daily_show
```

### As a Python module

`import` it for programmatic use: `wake_if_needed()`, `upload_and_show(path, gallery, prefix)`, `cleanup_old(prefix, keep)`, `device_info()`, `state()`, `wait_ready()`.

## Recommended workflow

1. `uv run "$CLIENT" info` → learn `width`, `height`, current `gallery` / `image` / `battery`.
2. Locally resize (and optionally dither) the image to `width × height`, save as JPEG.
3. `uv run "$CLIENT" upload <file> --prefix <app>` → pushes, displays, verifies; returns the generated filename.
4. `uv run "$CLIENT" cleanup --prefix <app>_ --keep <that filename>` to keep the gallery tidy.

## BLE wake details

E-ink Canvas sleeps aggressively; when HTTP times out, `client.py` (or any command that needs the device) triggers a BLE wake pulse via `wake.py` next to this skill. Details, in case wake fails and you need to debug:

- **Protocol** (reverse-engineered from the ARPOBOT-BLOOMIN8 HA component): GATT write-without-response to char `0000f001-0000-1000-8000-00805f9b34fb`, pulse `0x01` → **hold 500ms** → `0x00` → disconnect. The HA reference uses a 1ms gap; **empirically that is too short** for firmware 1.8.35 — `wake.py` uses 500ms, which matches a real button-press cadence and wakes reliably.
- **macOS**: hardware MACs are hidden, so `wake.py` discovers by name (`Bloomin8`). First run may need Bluetooth permission for the terminal app (System Settings → Privacy & Security → Bluetooth). The Mac must be within ~10 m of the Canvas.
- Manual invocation: `uv run --with bleak python "$(find ~/.claude/skills -name wake.py | head -1)"`
- After wake, the device stays reachable for ~`max_idle` seconds (default 120s, see `info`). For longer sessions, run `whistle` periodically.

## Common gotchas

- **Connection timeout** → device is asleep; `client.py` wakes it automatically. If BLE wake fails, wake via the Bloomin8 mobile app — do not retry-loop blindly.
- **Never reuse a filename** (observed on firmware 1.8.35): the device caches the processed image by file path. Uploading new content under an existing filename displays the STALE image — and even deleting the file first, then re-uploading the same name, does not refresh the panel; `/state` still reaches `100`, so it looks like a success. `client.py upload` prevents this by timestamping every filename and verifying `/deviceInfo.image` afterwards.
- **JPEG only** for uploads — PNG/WebP will fail or render badly.
- **Wrong dimensions** → image gets stretched. Always pre-resize to match `info`'s `width × height`.
- **No bearer auth** → anyone on the LAN can control the Canvas. Keep it off untrusted networks.
- **No markdown rendering on-device** — this was a cloud-only feature. Render to a JPEG locally (e.g. with headless Chrome / Playwright / Pandoc → image) before uploading.
- **Aggressive sleep** → for a sequence of commands, run `whistle` periodically so the device doesn't doze off mid-flow.

## Status codes from `/state`

- `100` — Ready / idle (operation complete)
- Other values represent in-progress work; `wait-ready` polls until `100`.

## Raw HTTP API reference

For debugging or environments without `uv` — `client.py` wraps exactly these endpoints:

```bash
# Status / system
curl -sS "http://$BLOOMIN8_LAN_IP/deviceInfo" | jq
curl -sS "http://$BLOOMIN8_LAN_IP/state" | jq
curl -sS "http://$BLOOMIN8_LAN_IP/whistle"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/sleep"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/reboot"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/clearScreen"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/settings" -H "Content-Type: application/json" -d '{"max_idle":300}'

# Display — /show (play_type: 0 = single image, 1 = gallery, 2 = playlist; optional "dither": 0 Floyd-Steinberg / 1 JJN)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" -H "Content-Type: application/json" -d '{"play_type":0,"image":"/gallerys/default/f1.jpg"}'
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" -H "Content-Type: application/json" -d '{"play_type":1,"gallery":"default","duration":120}'
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" -H "Content-Type: application/json" -d '{"play_type":2,"playlist":"daily_show"}'
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/showNext"

# Upload / delete (JPEG only; never reuse a filename — see gotchas)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/upload?filename=img_$(date +%s).jpg&gallery=default&show_now=1" -F "image=@/path/to/img.jpg"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/image/uploadMulti?gallery=default&override=1" -F "images=@a.jpg" -F "images=@b.jpg"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/image/delete?image=img.jpg&gallery=default"

# Galleries
curl -sS "http://$BLOOMIN8_LAN_IP/gallery/list" | jq
curl -sS -X PUT "http://$BLOOMIN8_LAN_IP/gallery?name=vacation"
curl -sS "http://$BLOOMIN8_LAN_IP/gallery?gallery_name=default&offset=0&limit=50" | jq
curl -sS -X DELETE "http://$BLOOMIN8_LAN_IP/gallery?name=vacation"

# Playlists
curl -sS "http://$BLOOMIN8_LAN_IP/playlist/list" | jq
curl -sS -X PUT "http://$BLOOMIN8_LAN_IP/playlist" -H "Content-Type: application/json" -d '{"name":"daily_show","type":"duration","list":[{"name":"/gallerys/default/f1.jpg","duration":40,"time":""}]}'
curl -sS "http://$BLOOMIN8_LAN_IP/playlist?name=daily_show" | jq
curl -sS -X DELETE "http://$BLOOMIN8_LAN_IP/playlist?name=daily_show"
```

**Advanced — `/image/dataUpload`** (not wrapped by client.py): upload pre-processed, dithered raw image data for fast direct-to-screen rendering, bypassing the device's JPEG decode + dithering. Rarely needed — prefer `upload` unless you are reproducing the device's native dithering pipeline exactly. Query param `filename` (required), multipart field `dithered_image` (binary).

```bash
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/image/dataUpload?filename=frame.bin" -F "dithered_image=@/path/to/dithered.bin"
```

## Reference

Official API documentation: <https://bloomin8.readme.io/>
