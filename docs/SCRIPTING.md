# Scripting Guide — No AI Required

Everything in this repo is a plain [`uv`](https://docs.astral.sh/uv/)-run Python script or `curl` call. Claude Code is a convenient way to *drive* them with natural language and judgment (choosing symbols, wording a countdown title, picking a cron schedule), but nothing here has a hard runtime dependency on an LLM. This page is the reference for calling the scripts directly — cron, systemd timers, Home Assistant, or any other automation.

If you *are* using Claude Code, see the main [README](../README.md) instead.

## Setup

```bash
export BLOOMIN8_LAN_IP=192.168.0.87        # required
export BLOOMIN8_BLE_NAME=Bloomin8          # optional, for BLE wake by name (macOS default path)
# export BLOOMIN8_BLE_MAC=AA:BB:CC:DD:EE:FF  # optional, Linux/Windows alternative to BLE_NAME
```

These must be set wherever the script actually runs — a login shell profile is not sourced by cron or a systemd unit, so export them inline or in the unit's `Environment=` / crontab line (examples below).

All commands below assume you're in `skills/bloomin8-canvas/` (installed at `~/.claude/skills/bloomin8-canvas/` if you used the `skills` CLI, or anywhere if you just cloned this repo — none of it requires the Claude skill machinery).

## `scripts/client.py` — device control

The single entry point for everything the frame can do. Full command list: `uv run scripts/client.py --help`. Highlights:

```bash
uv run scripts/client.py info                                   # /deviceInfo — width, height, battery, current image
uv run scripts/client.py state                                  # /state — {status, msg}; 100 = Ready
uv run scripts/client.py upload /path/to/image.jpg --prefix myapp   # upload + display; wakes the device, verifies the result
uv run scripts/client.py cleanup --prefix myapp_ --keep myapp_20260707_162811.jpg
uv run scripts/client.py sleep / reboot / clear-screen / whistle
uv run scripts/client.py show-gallery vacation --duration 120
uv run scripts/client.py gallery-create vacation
uv run scripts/client.py playlist-set '{"name":"daily","type":"duration","list":[...]}'
```

Every command prints one line of JSON and exits non-zero on failure — easy to pipe into `jq` or check `$?` in a script. See `skills/bloomin8-canvas/SKILL.md` for the full command reference and the raw HTTP API underneath.

It's also an importable Python module if you're writing a longer script:

```python
import sys
sys.path.insert(0, "/path/to/skills/bloomin8-canvas/scripts")
from client import upload_and_show, cleanup_old, device_info

result = upload_and_show("/tmp/frame.jpg", prefix="myapp")
cleanup_old(prefix="myapp_", keep=result["filename"])
```

## Dashboard generators

Each generator is `fetch data → render HTML → screenshot → JPEG`, independent of `client.py` — pipe its `--out` into `client.py upload` yourself.

```bash
# Crypto prices (Binance, no key)
uv run scripts/render.py --symbols BTC,ETH,BNB,ADA --range 24h --out /tmp/crypto.jpg

# Countdown / anniversary (Met Museum artwork background, no key)
uv run scripts/countdown.py --date 2026-12-31 --title "New Year's Eve" --out /tmp/countdown.jpg

# Weather (Open-Meteo, no key)
uv run scripts/weather.py --lat 25.033 --lon 121.565 --city Taipei --out /tmp/weather.jpg
```

Full flag reference and design notes for each: `references/crypto-dashboard.md`, `references/countdown.md`, `references/weather.md`.

All three accept `--width`/`--height` (auto-detected from the device if omitted), `--orientation portrait|landscape`, and `--rotate cw|ccw` for landscape mounting.

## Putting it together: a refresh script

```bash
#!/bin/bash
set -euo pipefail
export BLOOMIN8_LAN_IP=192.168.0.87
SKILL=/path/to/skills/bloomin8-canvas   # or ~/.claude/skills/bloomin8-canvas

OUT=/tmp/weather.jpg
uv run "$SKILL/scripts/weather.py" --city Taipei --out "$OUT"
FN=$(uv run "$SKILL/scripts/client.py" upload "$OUT" --prefix weather | jq -r .filename)
uv run "$SKILL/scripts/client.py" cleanup --prefix weather_ --keep "$FN"
rm -f "$OUT"
```

### cron

```cron
*/30 6-23 * * * BLOOMIN8_LAN_IP=192.168.0.87 /path/to/refresh-weather.sh >> /tmp/bloomin8_weather.log 2>&1
```

cron runs with a minimal environment and no shell profile — use absolute paths for `uv` (`which uv` to find it) and export every env var the script needs inline, as above.

### systemd timer

```ini
# /etc/systemd/system/bloomin8-weather.service
[Unit]
Description=Refresh Bloomin8 weather dashboard

[Service]
Type=oneshot
Environment=BLOOMIN8_LAN_IP=192.168.0.87
ExecStart=/path/to/refresh-weather.sh
```

```ini
# /etc/systemd/system/bloomin8-weather.timer
[Timer]
OnCalendar=*:0/30
Persistent=true

[Install]
WantedBy=timers.target
```

## Home Assistant integration

Two ways to trigger these scripts from Home Assistant, depending on where HA runs relative to the frame's LAN:

**If Home Assistant OS/Supervised runs on the same LAN** (most common — HA typically doesn't have `uv` installed in its container, so call out to a host that does):
- Point HA at a small HTTP endpoint of your own (e.g. a tiny Flask/FastAPI wrapper, or just `ssh` from HA into a machine that has `uv` and this repo) via the [`shell_command`](https://www.home-assistant.io/integrations/shell_command/) integration:
  ```yaml
  # configuration.yaml
  shell_command:
    bloomin8_weather: >
      ssh user@host "BLOOMIN8_LAN_IP=192.168.0.87 /path/to/refresh-weather.sh"
  ```
  Trigger it from an automation (time pattern, sun event, etc.) with `service: shell_command.bloomin8_weather`.

**If you'd rather not shell out**: wrap `client.py` and the dashboard scripts in a minimal FastAPI/Flask service running on a machine with `uv`, and call it from HA via [`rest_command`](https://www.home-assistant.io/integrations/rest_command/). This also makes it easy to pass HA's own weather/calendar data into a dashboard instead of re-fetching from Open-Meteo.

Either way, the frame itself has no concept of Home Assistant — you're just calling the same `client.py` / `weather.py` / etc. commands documented above from an HA automation instead of a crontab.

## What you lose without an AI agent

Nothing functional — every script here is self-contained and does not call out to any LLM. What you *do* lose is the judgment layer: Claude Code reads `SKILL.md` and the `references/*.md` files to pick sensible defaults, wake-then-retry sequencing, and natural-language control ("switch to the 7-day view", "put a Christmas countdown up"). Scripting this yourself means you own that decision-making — the flags and JSON contracts above are exactly what the skill uses under the hood, so there's no hidden behavior to reverse-engineer.
