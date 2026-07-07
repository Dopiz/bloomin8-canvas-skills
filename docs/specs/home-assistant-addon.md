# Spec: Bloomin8 Canvas — Home Assistant Add-on

> Status: draft for implementation. Audience: an AI/engineer implementing this from scratch. Self-contained, but the validated reference implementation of every device interaction and widget renderer lives in this repo under `skills/bloomin8-canvas/` (`scripts/client.py`, `scripts/render.py|weather.py|countdown.py`, `scripts/eink_render.py`, `assets/*.html`) — reuse that Python code directly rather than porting it. Protocol reference: <https://bloomin8.readme.io/> and the OpenAPI spec in <https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component>.

## 1. Goal

A Home Assistant **add-on** (Supervisor-managed Docker container, targeting HA OS / Supervised) that turns the Bloomin8 e-ink Canvas into a first-class HA citizen:

1. **Rendering + push service** — the three dashboards from this repo (crypto, weather, countdown) plus arbitrary image push, exposed over a local REST API so HA automations can refresh the frame.
2. **Built-in scheduler** — cron-style recurring refreshes configured in the add-on, so the frame stays current even without authoring HA automations.
3. **HA-native surface** — entities via MQTT discovery (battery, currently displayed image, reachability) and easy `rest_command` recipes, so the frame shows up on dashboards and can join any automation ("meeting starts in 10 min → push countdown", "rain probability > 60 % → refresh weather").

Relationship to prior art: the community ARPOBOT custom *integration* provides basic device control entities. This add-on is complementary — its value is the **rendering pipeline + scheduler** (headless Chromium, templates, data fetching), which an integration cannot reasonably ship. Do not fork or replace the integration; users may run both.

## 2. Non-goals (v1)

- Publishing to the community add-on store (structure the repo so it *can* be a custom add-on repository, i.e. installable via "Add repository" URL, but store submission is out of scope).
- A rich web UI. Ingress serves a minimal status/trigger page only; configuration happens through the add-on Options schema and the REST API.
- Supporting multiple frames (single device per add-on instance; users can run two instances if needed).
- Editing widget templates through the UI (templates ship in the image; advanced users can mount overrides — see §6).

## 3. Architecture

```
homeassistant-addon/
├── config.yaml            # add-on manifest: options schema, ingress, host_dbus, ports
├── Dockerfile             # python:3.12-slim + playwright chromium + bluez/dbus client libs
├── rootfs/
│   └── etc/services.d/bloomin8/run   # s6-overlay service: uvicorn app
└── app/
    ├── main.py            # FastAPI app (REST API + ingress page)
    ├── scheduler.py       # APScheduler cron jobs from options + API-defined schedules
    ├── mqtt.py            # optional MQTT discovery publisher (paho-mqtt)
    └── vendor/            # copied from this repo: client.py, eink_render.py,
                           # render.py, weather.py, countdown.py, assets/*.html
```

- **Runtime**: Python 3.12, FastAPI + uvicorn, APScheduler for cron, Playwright + bundled Chromium for HTML→JPEG (same pipeline as the reference scripts; the image will be ~1 GB — acceptable for an add-on, note it in the README).
- **Reuse, don't port**: `client.py` already exposes `wake_if_needed() / upload_and_show() / cleanup_old() / device_info() / wait_ready()` as importable functions, and each dashboard script has a `main()` plus small pure functions. Vendor these files into the image; adapt only the BLE-wake invocation (see §5.2) and path handling. All firmware gotchas are already encoded there — do not re-implement upload logic.
- **Device discovery**: LAN IP comes from add-on options (`lan_ip`). No mDNS in v1.

## 4. Add-on manifest (config.yaml) essentials

- `ingress: true` (minimal status page), `host_network: false` with mapped port for the REST API **bound to the internal docker network only** (HA reaches it as `http://<addon-slug>:8099`); never expose the port on the host — the device API downstream is unauthenticated, so the add-on API must not become a LAN-wide relay. If a host port is offered at all, default it to disabled.
- `host_dbus: true` — required for BLE wake through the host's BlueZ (bleak talks to `org.bluez` over the system D-Bus). Document that HAOS's own Bluetooth integration may hold the adapter; wake attempts should tolerate `InProgress`/busy errors with one retry.
- Options schema (with sensible defaults):

```yaml
lan_ip: "192.168.0.87"          # required
ble_name: "Bloomin8"
timezone: ""                     # empty = container TZ from HA
mqtt: { enabled: true }          # uses the Supervisor-provided MQTT service if available
defaults:
  city: "Taipei"
  lat: 25.033
  lon: 121.565
  orientation: "portrait"        # portrait | landscape
  rotate: "cw"                   # cw | ccw (landscape mounting direction)
schedules:                       # declarative schedules; API can add more at runtime
  - { widget: weather, cron: "*/30 7-23 * * *", enabled: true }
  - { widget: crypto,  cron: "0 * * * *", enabled: false, params: { symbols: "BTC,ETH" } }
```

## 5. Device protocol summary (validated on firmware 1.8.35, EL133UF1 1200×1600)

The vendored `client.py` handles all of this; listed here so the implementer understands *why* it must not be bypassed:

1. **Never reuse an upload filename** — firmware caches the processed image by file path; a reused name displays stale content while `/state` still reports success (even after delete-then-reupload). Uploads are always `<prefix>_<timestamp>.jpg`, then `/state` polled to `100`, then `GET /deviceInfo` → `.image` verified to end with the new name.
2. **Aggressive sleep** — the device leaves the network after `max_idle` (default 120 s). Every operation is wake-if-needed first; long flows ping `GET /whistle`.
3. **BLE wake** — GATT write-without-response to `0000f001-0000-1000-8000-00805f9b34fb`: `0x01` → hold 500 ms → `0x00` → disconnect. Discovery by advertised name. The pulse can "fail" at the BLE layer (disconnect-time exceptions, non-zero exits) even when it landed — **the only success signal is HTTP answering afterwards**, so poll `/deviceInfo` up to ~45 s and judge on that.
4. **JPEG only, exact panel dimensions**; landscape = render swapped then rotate ±90° back to portrait-native.
5. **Cleanup guard** — deleting old `<prefix>_*` images requires a non-empty keep-filename (a failed upload must never cascade into deleting the currently displayed file).
6. **No auth on the device** — see §4 network constraints.

### 5.2 BLE inside the container

`client.py` currently shells out to `wake.py` found under `~/.claude/skills/`; in the add-on, vendor `wake.py` alongside and change the lookup to a relative path. `bleak` requires the system D-Bus socket (`host_dbus: true`) and BlueZ on the host — available on HAOS. Fallback path when BLE is unavailable (no adapter, permission denied): surface a clear error suggesting the user extend `max_idle` / `sleep_duration` via `POST /device/settings` so scheduled runs hit an awake device, or wake via the vendor mobile app.

## 6. REST API (served by FastAPI; all responses JSON)

```
GET  /health                      → {ok, device_reachable, battery, image}
GET  /device/info                 → proxied /deviceInfo (wakes device)
POST /device/wake|sleep|reboot|clear-screen
POST /device/settings             body: any subset {name, sleep_duration, max_idle, idx_wake_sens}
POST /render/weather              body: {city?, lat?, lon?, orientation?, rotate?, push=true}
POST /render/crypto               body: {symbols?, range?, orientation?, rotate?, push=true}
POST /render/countdown            body: {date, title?, bg_query?, orientation?, rotate?, push=true}
POST /push                        multipart image → resized/validated → upload_and_show
GET  /schedules  /  POST /schedules  /  DELETE /schedules/{id}
GET  /runs?limit=50               → run history (widget, started, duration, ok, error, filename)
```

`push=false` on render endpoints returns the JPEG bytes instead of pushing (useful for HA notifications/preview). Every successful push runs `cleanup_old(prefix=<widget>_, keep=<new file>)`.

Ingress page: current device card (battery, displayed image, reachability), one "Run now" button per configured schedule, last 20 runs table. Plain HTML + htmx or vanilla JS; no build step.

## 7. HA integration surface

**MQTT discovery** (when the MQTT service is available; auto-configured via Supervisor's `services: [mqtt:need]`): publish a device "Bloomin8 Canvas" with:

- `sensor.bloomin8_battery` (%), `sensor.bloomin8_current_image`, `binary_sensor.bloomin8_reachable`
- `button.bloomin8_refresh_weather` / `_crypto` / `_countdown` / `_wake` — pressing publishes a command the add-on consumes to trigger the corresponding render/push.
- State refresh: poll the device every 5 min *only if awake* (do not wake it just to poll — that would defeat its sleep schedule); mark unreachable otherwise.

**Automation recipes** (document in the add-on README):

```yaml
rest_command:
  bloomin8_weather:
    url: "http://<addon-slug>:8099/render/weather"
    method: post
    payload: '{"city": "Taipei"}'
    content_type: application/json

automation:
  - alias: Frame shows weather every morning
    trigger: { platform: time, at: "07:00:00" }
    action: { service: rest_command.bloomin8_weather }
```

## 8. Milestones & acceptance criteria

**M1 — Container + device client**: add-on installs from a custom repository URL, options schema works, `GET /device/info` wakes a sleeping frame via BLE from within the container (HAOS on a machine within BLE range), `POST /push` displays an uploaded image with display verification. Acceptance: two consecutive pushes both visibly refresh the physical panel (proves the fresh-filename path survived vendoring).

**M2 — Renderers**: all three `/render/*` endpoints produce correct JPEGs inside the container (Playwright/Chromium works under s6), portrait + landscape, and `push=true` end-to-end updates the panel. Acceptance: byte-for-byte plausible parity with the repo scripts' output for fixed inputs (allowing font rendering diffs), verified by eye at 1200×1600.

**M3 — Scheduler + run history**: declarative schedules from options fire on time (container TZ correct!), overlap-skip, one retry after 60 s, runs recorded and visible via `GET /runs` and the ingress page. Acceptance: a `*/5` weather schedule survives an add-on restart and a HA host reboot.

**M4 — MQTT discovery + docs**: entities appear automatically on a stock HA with Mosquitto; README documents rest_command/automation recipes, BLE limitations, and the "extend max_idle if no Bluetooth" fallback.

## 9. Risks / open questions

- **BLE from the container** is the highest-risk item: adapter contention with HA's Bluetooth integration, D-Bus permissions, HAOS quirks. De-risk it first (M1). If it proves flaky, the documented fallback (longer `max_idle` / vendor-app wake) keeps everything else functional.
- **Image size** (~1 GB with Chromium) — acceptable, but pin Playwright and use `--with-deps=false` + explicit apt deps to keep it from growing further. Consider `chromium` from Debian as a slimmer alternative if Playwright's bundled build misbehaves under QEMU builds for aarch64.
- **Multi-arch**: HAOS runs on amd64 and aarch64 (RPi). Playwright Chromium supports both on Debian-based images; CI must build both.
- **Time zones**: countdown day-math and weather "Now" anchoring are local-time sensitive; make sure the container inherits HA's TZ (Supervisor provides it) and cover with a test.
