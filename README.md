# Bloomin8 Canvas — Claude Code Skill

> A [Claude Code](https://claude.com/claude-code) Agent Skill that controls a Bloomin8 colour e-ink frame directly over the LAN. **Device-to-device — no cloud, no auth.**

This repo provides an Agent Skill that lets Claude Code push images, manage galleries and playlists, sleep/wake/reboot the device, and query frame status — all over a direct LAN connection. The cloud `einkshot.run.app` API is intentionally not used.

> ⚠️ **Work in progress / personal project.** Validated on firmware `1.8.35` (EL133UF1, 1200×1600). Other firmware versions or models may behave differently — use at your own discretion.

## Installation

Install with the [`skills`](https://github.com/vercel-labs/skills) CLI:

```bash
npx skills add Dopiz/bloomin8-canvas-skills
```

This clones the repo, detects `skills/bloomin8-canvas/SKILL.md`, and copies it into Claude Code's skills directory at `~/.claude/skills/bloomin8-canvas`. `wake.py` is copied alongside the skill and pulls its `bleak` dependency on the fly through `uv` at runtime — no extra packages to install.

Verify BLE wake works (requires `uv`):

```bash
uv run --with bleak python ~/.claude/skills/bloomin8-canvas/wake.py
```

After that, the skill triggers automatically whenever you mention Bloomin8 / Canvas / the e-ink frame, or ask to push an image, manage galleries, or check status in a Claude Code conversation.

## Configuration

The skill and `wake.py` read the target device from environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `BLOOMIN8_LAN_IP` | ✅ | The frame's LAN IP, e.g. `192.168.0.87` |
| `BLOOMIN8_BLE_NAME` | — | Device name used for BLE scan matching, defaults to `Bloomin8` |
| `BLOOMIN8_BLE_MAC` | — | Explicit BLE MAC (works on Linux/Windows; macOS hides hardware MACs, so name matching is used instead) |

> Environment variables must be visible to non-interactive zsh. Put them in `~/.zshrc` and `source ~/.zshrc` at the start of any session that needs them.

```bash
# ~/.zshrc
export BLOOMIN8_LAN_IP=192.168.0.87
export BLOOMIN8_BLE_NAME=Bloomin8
```

Verify connectivity:

```bash
curl -sS --max-time 3 "http://$BLOOMIN8_LAN_IP/deviceInfo" >/dev/null \
  && echo OK || echo "asleep — BLE wake needed"
```

## Available Skills

- **[bloomin8-canvas](skills/bloomin8-canvas/SKILL.md)** — LAN-direct control of the Bloomin8 Canvas: `deviceInfo` / `state` status queries, `/upload` image push, `/show` single/gallery/playlist playback, gallery and playlist CRUD, `sleep` / `reboot` / `clearScreen` / `settings`, plus BLE wake recovery when the device is asleep.

## Usage

Once triggered, the skill is driven with natural language, for example:

- "What's the status of my bloomin8 frame?"
- "Push this image to the frame."
- "Create a vacation gallery, upload these photos, and start a slideshow."
- "Put the frame to sleep / reboot it."

The underlying HTTP API (see [SKILL.md](skills/bloomin8/SKILL.md) for details):

```bash
# Status
curl -sS "http://$BLOOMIN8_LAN_IP/deviceInfo" | jq

# Upload and display immediately (JPEG only; resize to width×height locally first)
curl -sS -X POST \
  "http://$BLOOMIN8_LAN_IP/upload?filename=hello.jpg&gallery=default&show_now=1" \
  -F "image=@/path/to/hello.jpg"
```

For best e-ink rendering, resize and dither locally before upload:

```bash
magick input.png -resize 1200x1600^ -gravity center -extent 1200x1600 \
  -dither FloydSteinberg -colors 64 -quality 90 output.jpg
```

## How It Works

- **LAN-direct**: the frame exposes an unauthenticated HTTP API on the local network; pushing content is device-to-device with **no cloud involvement**.
- **Aggressive sleep**: the e-ink frame sleeps frequently, so HTTP times out. When that happens `wake.py` sends a BLE wake pulse (GATT write to `0000f001-…`, `0x01` → hold 500ms → `0x00`); HTTP usually responds again within 1–3 seconds. The 500ms hold mimics a real button press and wakes firmware 1.8.35 reliably.
- **Stay awake**: after waking, the device stays reachable for about `max_idle` seconds (see `/deviceInfo`); for a sequence of operations, `GET /whistle` periodically to keep it from dozing off.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| HTTP timeout | Device is asleep → run `wake.py` for a BLE wake; don't blindly retry-loop |
| BLE can't find the device | Make sure the Mac is within ~10 m; first run needs Bluetooth permission for the terminal (System Settings → Privacy & Security → Bluetooth) |
| Distorted image | `/upload` accepts JPEG only and the image must be pre-resized to `/deviceInfo`'s `width × height` |
| Anyone can control it | The API has no bearer auth — keep it off untrusted networks |

## Reference

Official API documentation: <https://bloomin8.readme.io/>

## License

Personal project, no warranty of any kind. Use only on devices you own and on trusted networks.
