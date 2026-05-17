---
name: bloomin8-canvas
description: Control a Bloomin8 e-ink Canvas directly over the LAN (no cloud, no auth). Trigger when the user mentions Bloomin8, Canvas, e-ink frame, or asks to push an image, manage galleries/playlists, sleep/wake/reboot the device, or check its status.
allowed-tools: Bash(curl:*), Bash(jq:*), Bash(printenv:*), Bash(test:*), Bash(file:*), Bash(magick:*), Bash(convert:*), Bash(sips:*), Bash(uv:*), Bash(source:*)
---

# Bloomin8 Canvas — LAN Direct API

The Canvas exposes an unauthenticated HTTP API on the local network. **Pushing goes device-to-device — no cloud involvement.** This is the preferred path; the cloud `einkshot.run.app` API is intentionally not used.

## Setup

- **Base URL**: `http://$BLOOMIN8_LAN_IP` (e.g. `192.168.0.87`)
- **No auth required** — pure LAN trust model.
- Same network segment required (no VLAN isolation between client and Canvas).
- For BLE wake (see below): `BLOOMIN8_BLE_MAC` and optional `BLOOMIN8_BLE_NAME` (default `Bloomin8`).

Env vars must be visible to non-interactive zsh — put them in `~/.zshenv`, not `~/.zshrc`, OR `source ~/.zshrc` at the start of any session that needs them.

Verify reachability:
```bash
curl -sS --max-time 3 "http://$BLOOMIN8_LAN_IP/deviceInfo" >/dev/null && echo OK || echo "asleep — run BLE wake (next section)"
```

## BLE wake (sleep recovery)

E-ink Canvas sleeps aggressively. When HTTP times out, send a BLE wake pulse using `wake.py` next to this skill:

```bash
uv run --with bleak python ~/.claude/skills/bloomin8-canvas/wake.py
# then wait ~1s and the device should be reachable over HTTP
```

**Protocol** (reverse-engineered from the ARPOBOT-BLOOMIN8 HA component):
- GATT write to char `0000f001-0000-1000-8000-00805f9b34fb` (write-without-response)
- Pulse `0x01` → **hold 500ms** → `0x00` → disconnect
- ⚠️ The HA reference uses a 1ms gap; **empirically that is too short** for firmware 1.8.35 — `wake.py` uses 500ms which matches a real button-press cadence and wakes reliably.

**macOS gotchas**:
- macOS hides hardware MACs; `wake.py` discovers by name (`Bloomin8`) and works regardless of platform.
- First run may need Bluetooth permission for the terminal app (System Settings → Privacy & Security → Bluetooth). Once granted, no further prompts.
- Mac must be within ~10 m of the Canvas.

**Standard wake-then-act flow**:
```bash
# 1. Try direct HTTP
if ! curl -sS --max-time 3 "http://$BLOOMIN8_LAN_IP/deviceInfo" >/dev/null; then
  # 2. BLE wake (10s scan + 1s connect + 0.5s pulse)
  uv run --with bleak python ~/.claude/skills/bloomin8-canvas/wake.py || {
    echo "BLE wake failed — wake via Bloomin8 app"; exit 1
  }
  # 3. Poll HTTP (usually responds in 1-3s)
  for i in $(seq 1 30); do
    curl -sS --max-time 2 "http://$BLOOMIN8_LAN_IP/deviceInfo" >/dev/null && break
    sleep 1
  done
fi
# 4. ... do whatever operation
```

After wake, the device stays reachable for ~`max_idle` seconds (default 120s, see `/deviceInfo`). For longer sessions, periodically `GET /whistle` to keep it awake.

## Endpoints

### Status / system

```bash
# Full device info — width, height, battery, gallery, playlist, IP, screen model, etc.
curl -sS "http://$BLOOMIN8_LAN_IP/deviceInfo" | jq

# Task state ({status: int, msg: string})
curl -sS "http://$BLOOMIN8_LAN_IP/state" | jq

# Keep-alive (prevents sleep)
curl -sS "http://$BLOOMIN8_LAN_IP/whistle"

# Sleep / reboot / clear screen (white)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/sleep"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/reboot"
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/clearScreen"

# Update device settings (any subset of fields)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/settings" \
  -H "Content-Type: application/json" \
  -d '{"name":"Living Room","sleep_duration":86400,"max_idle":300,"idx_wake_sens":3}'
```

### Display control — `/show`

Drives playback: single image, gallery slideshow, or playlist.

```bash
# Single image (already on device)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" \
  -H "Content-Type: application/json" \
  -d '{"play_type":0,"image":"/gallerys/default/f1.jpg"}'

# Gallery slideshow (interval in seconds)
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" \
  -H "Content-Type: application/json" \
  -d '{"play_type":1,"gallery":"default","duration":120}'

# Playlist
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/show" \
  -H "Content-Type: application/json" \
  -d '{"play_type":2,"playlist":"daily_show"}'

# Skip to next in current queue
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/showNext"
```

Optional in `/show`: `"dither": 0` (Floyd-Steinberg) or `1` (JJN).

### Upload an image — `/upload`

**Only JPEG is accepted.** The image should already match the device's `width × height` from `/deviceInfo` (e.g. EL133UF1 = 1200×1600). Pre-process locally before upload.

```bash
# Upload + display immediately
curl -sS -X POST \
  "http://$BLOOMIN8_LAN_IP/upload?filename=hello.jpg&gallery=default&show_now=1" \
  -F "image=@/path/to/hello.jpg"

# Upload only (don't display)
curl -sS -X POST \
  "http://$BLOOMIN8_LAN_IP/upload?filename=hello.jpg&gallery=default" \
  -F "image=@/path/to/hello.jpg"

# Bulk upload (multiple files, optional override of same-name files)
curl -sS -X POST \
  "http://$BLOOMIN8_LAN_IP/image/uploadMulti?gallery=default&override=1" \
  -F "images=@a.jpg" -F "images=@b.jpg"

# Delete an image
curl -sS -X POST "http://$BLOOMIN8_LAN_IP/image/delete?image=hello.jpg&gallery=default"
```

For best e-ink rendering, pre-dither locally (ImageMagick):
```bash
magick input.png -resize 1200x1600^ -gravity center -extent 1200x1600 \
  -dither FloydSteinberg -colors 64 -quality 90 output.jpg
```

**Advanced — `/image/dataUpload`**: upload pre-processed, dithered raw image data for fast direct-to-screen rendering (bypasses the device's JPEG decode + dithering). Rarely needed — prefer `/upload` unless you are reproducing the device's native dithering pipeline exactly. Query param `filename` (required), multipart field `dithered_image` (binary).

```bash
curl -sS -X POST \
  "http://$BLOOMIN8_LAN_IP/image/dataUpload?filename=frame.bin" \
  -F "dithered_image=@/path/to/dithered.bin"
```

### Galleries

```bash
# List all galleries
curl -sS "http://$BLOOMIN8_LAN_IP/gallery/list" | jq

# Create empty gallery
curl -sS -X PUT "http://$BLOOMIN8_LAN_IP/gallery?name=vacation"

# List images in a gallery (pagination required)
curl -sS "http://$BLOOMIN8_LAN_IP/gallery?gallery_name=default&offset=0&limit=50" | jq

# Delete gallery (and all images inside!)
curl -sS -X DELETE "http://$BLOOMIN8_LAN_IP/gallery?name=vacation"
```

### Playlists

A playlist is an ordered list of image refs with per-item `duration` (seconds) OR a wall-clock `time` (string). `type` selects which field matters.

```bash
# List
curl -sS "http://$BLOOMIN8_LAN_IP/playlist/list" | jq

# Create / overwrite (duration-based)
curl -sS -X PUT "http://$BLOOMIN8_LAN_IP/playlist" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"daily_show",
    "type":"duration",
    "list":[
      {"name":"/gallerys/default/f1.jpg","duration":40,"time":""},
      {"name":"/gallerys/default/f2.jpg","duration":40,"time":""}
    ]
  }'

# Get content
curl -sS "http://$BLOOMIN8_LAN_IP/playlist?name=daily_show" | jq

# Delete
curl -sS -X DELETE "http://$BLOOMIN8_LAN_IP/playlist?name=daily_show"
```

## Recommended workflow

1. `GET /deviceInfo` → learn `width`, `height`, current `gallery`, `image`, `battery`.
2. Locally resize + dither the image to match `width × height`, save as JPEG.
3. `POST /upload?filename=X.jpg&gallery=default&show_now=1` to push and display.
4. `GET /state` to confirm task completion (`status: 100` = Ready).

## Common gotchas

- **Connection timeout** → device is asleep. Wake via Bluetooth in the mobile app; do not retry-loop blindly.
- **No bearer auth** → anyone on the LAN can control the Canvas. Keep it off untrusted networks.
- **JPEG only** for `/upload` — PNG/WebP will fail or render badly.
- **Wrong dimensions** → image gets stretched. Always pre-resize to match `/deviceInfo` `width × height`.
- **No markdown rendering on-device** — this was a cloud-only feature. Render to a JPEG locally (e.g. with headless Chrome / Playwright / Pandoc → image) before uploading.
- **Aggressive sleep** → after operations, `GET /whistle` periodically if you need a sequence of commands without the device dozing off.

## Status codes from `/state`

- `100` — Ready / idle (operation complete)
- Other values represent in-progress work; poll until `100` if you need to chain commands.

## Reference

Official API documentation: <https://bloomin8.readme.io/>
