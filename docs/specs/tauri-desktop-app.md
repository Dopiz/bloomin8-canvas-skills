# Spec: Bloomin8 Canvas Desktop App (Tauri)

> Status: draft for implementation. Audience: an AI/engineer implementing this from scratch. This document is self-contained, but the reference implementation of every device interaction lives in this repo under `skills/bloomin8-canvas/` (`scripts/client.py`, `scripts/*.py`, `assets/*.html`) — treat those as the validated ground truth, and the official API docs at <https://bloomin8.readme.io/> plus the OpenAPI spec at <https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/blob/main/openapi.yaml> as the protocol reference.

## 1. Goal

A cross-platform desktop app (macOS first; Windows/Linux should work but are not release-blocking) that controls a Bloomin8 colour e-ink Canvas over the LAN:

1. **Device control** — status/battery, push an image, browse/manage galleries and playlists, sleep/wake/reboot/clear, device settings.
2. **Widgets** — render the three dashboards already implemented in this repo (crypto prices, weather, countdown) plus a plain "photo" source, with live preview before pushing.
3. **Scheduler** — recurring refreshes ("weather every 30 min, 07:00–23:00", "crypto every hour", "countdown daily at 06:00") that run in the background while the app is running (menu-bar/tray resident), with run history and failure surfacing.

No cloud services: the device API is LAN-only and unauthenticated; widget data comes from free public APIs (Binance, Open-Meteo, Met Museum). The app must work fully offline from any vendor account.

## 2. Non-goals (v1)

- Multi-device fleets (design the config so a device list is *possible* later, but ship single-device).
- Editing the widget HTML templates in-app (ship them as bundled assets; power users can edit files).
- Running as a headless daemon / launchd service without the app open (tray-resident is enough).
- Reimplementing the device's cloud features (markdown rendering, AI art, etc.).

## 3. Tech stack

- **Tauri v2**, Rust backend + TypeScript frontend (React + Tailwind suggested; not load-bearing).
- **BLE wake**: `btleplug` crate (works on macOS CoreBluetooth, Windows, BlueZ).
- **HTTP client**: `reqwest` (multipart upload support needed).
- **Scheduler**: `tokio-cron-scheduler` (cron expressions) driven from the Rust side so schedules fire even when the window is closed to tray.
- **Widget rendering**: render the bundled HTML templates *inside an offscreen/hidden Tauri WebView window* at exactly the panel's pixel dimensions, then capture to image. Preferred capture path: load the template in a hidden `WebviewWindow` sized to `width × height` with `devicePixelRatio` forced to 1, and use `html-to-image`/`html2canvas` in that page to produce a PNG data URL handed back to Rust via a Tauri command, where it is re-encoded to JPEG (quality ≈ 92). If capture fidelity turns out to be a problem, the fallback is the `headless_chrome` crate against a system Chrome — but treat that as plan B, since it adds an external dependency the Python reference already suffers from (Playwright + Chromium download).
- **Config/state**: JSON in the Tauri app-data dir (`device.json`, `schedules.json`, `history.jsonl`).

## 4. Device protocol (validated behavior — read carefully)

Base URL `http://<LAN_IP>`, no auth, same L2 network required. Firmware validated: 1.8.35 on EL133UF1 (1200×1600 portrait-native, Spectra 6 colour e-ink).

Endpoints (all wrapped by `scripts/client.py` in this repo — port its semantics, not just the raw calls):

| Operation | Endpoint |
|---|---|
| Device info (width, height, battery, current `image`, gallery, `max_idle`) | `GET /deviceInfo` |
| Task state (`{status, msg}`; `100` = Ready) | `GET /state` |
| Keep-alive (postpones sleep) | `GET /whistle` |
| Sleep / reboot / clear panel | `POST /sleep`, `/reboot`, `/clearScreen` |
| Settings (any subset: `name`, `sleep_duration`, `max_idle`, `idx_wake_sens`) | `POST /settings` (JSON) |
| Upload (multipart field `image`; query `filename`, `gallery`, `show_now`) | `POST /upload` |
| Delete image | `POST /image/delete?image=<name>&gallery=<g>` |
| Show single image / gallery slideshow / playlist | `POST /show` with `play_type` 0/1/2 |
| Next in queue | `POST /showNext` |
| Gallery list / create / delete / list-images (paginated) | `GET /gallery/list`, `PUT /gallery?name=`, `DELETE /gallery?name=`, `GET /gallery?gallery_name=&offset=&limit=` |
| Playlist CRUD | `GET /playlist/list`, `GET /playlist?name=`, `PUT /playlist` (JSON), `DELETE /playlist?name=` |

### 4.1 Firmware gotchas (each of these was discovered the hard way; encode them in the client layer, not in UI code)

1. **Never reuse an upload filename.** The firmware caches the processed image by file path. Re-uploading new content under an existing filename displays the STALE image — and even `image/delete` followed by re-upload of the same name does not refresh the panel, while `/state` still reaches `100` (looks like success). Always generate `<prefix>_<timestamp>.jpg`, and after a `show_now` upload, poll `/state` to `100` and then verify `GET /deviceInfo` → `.image` ends with the new filename. Treat a mismatch as failure.
2. **Aggressive sleep.** The device drops off the network entirely within `max_idle` seconds (default 120) of inactivity. Every operation must be preceded by: try `GET /deviceInfo` with a ~3 s timeout → on failure, send the BLE wake pulse → poll `/deviceInfo` up to ~30–45 s. During multi-step flows, hit `/whistle` periodically.
3. **BLE wake pulse**: GATT write-without-response to characteristic `0000f001-0000-1000-8000-00805f9b34fb`: write `0x01` → **hold 500 ms** → write `0x00` → disconnect. The 1 ms gap used by the HA reference component is too short for firmware 1.8.35; 500 ms wakes reliably. Discover the peripheral by advertised name (default `Bloomin8`) — macOS hides hardware MACs. First run needs the OS Bluetooth permission prompt; the machine must be within ~10 m.
4. **Wake success signal is HTTP, not BLE.** The BLE stack sometimes errors during post-pulse disconnect even though the pulse landed. Never treat the BLE call's error as fatal; poll `/deviceInfo` and let that decide.
5. **JPEG only**, pre-sized to exactly `deviceInfo.width × height`. Anything else fails or stretches. For landscape mounting: render at swapped dimensions, then rotate the bitmap ±90° back to portrait-native before upload (`--rotate cw|ccw` equivalent; direction depends on how the user mounted the frame).
6. **Cleanup must be guarded.** When deleting old `<prefix>_*` images after a successful upload, refuse to run if the "keep" filename is empty (a failed upload piped into cleanup would otherwise delete everything, including the image currently displayed).
7. **No auth** — anyone on the LAN can control the device. Do not expose any relay/proxy beyond localhost.

## 5. Widgets

Port the three renderers 1:1 from the Python reference; the HTML templates in `assets/` are the design source of truth and should be bundled as static app resources (they are plain HTML/CSS with `{{PLACEHOLDER}}` substitution — no JS, no external requests).

| Widget | Data source (no API key) | Reference script | Template |
|---|---|---|---|
| Crypto prices | Binance public API (`/api/v3/ticker/24hr`, `/api/v3/klines`; try mirror hosts `api`, `api1`, `api2` on failure) | `scripts/render.py` | `assets/crypto-template.html` |
| Weather | Open-Meteo forecast API (`current` + `hourly`, `timezone=auto`) | `scripts/weather.py` | `assets/weather-template.html` |
| Countdown | Met Museum collection API for public-domain painting backgrounds (search → object → `primaryImageSmall`), cached locally; or a user-supplied photo | `scripts/countdown.py` | `assets/countdown-template.html` |

Behavioral details worth preserving (all encoded in the reference scripts):

- Crypto: bare tickers get `USDT` appended; 1–3 symbols stack in one line, 4+ wrap into a 2-column grid (grid classes are computed and injected); sparkline includes a dashed line at the range-open price; price/badge typography is container-query clamped so nothing overflows dense grids.
- Weather: forecast strip is 5 slots × 3 h anchored at the current hour (first slot labelled "Now"); the background gradient theme is driven by the *condition group* (clear/cloudy/fog/rain/thunder/snow), not the clock; current-hour precipitation probability comes from the hourly series because `current` lacks it; a `--force-icon`-equivalent debug override lets the UI preview all themes.
- Countdown: day count = calendar-day difference; past dates flip the label to "days since"; artwork is picked deterministically by `abs(days) % results` so it rotates daily but is stable within a day; fetched art is cached (image + credit) and offline runs fall back to the cache.
- E-ink design rules baked into the templates: white text on saturated gradients or pure black-on-white; no large mid-gray areas (they dither into noise on Spectra 6); `vmin`-based sizing shared between portrait and landscape via `@media (orientation: landscape)`. **Layout budget caveat:** portrait height is ~133 vmin (vmin follows the shorter dimension), so templates have little vertical slack — forecast labels are `white-space: nowrap` deliberately; verify any template change at real pixel dimensions.

Failure policy: if a widget's data API is unreachable, fail the run visibly (log + UI badge). Never render stale or fabricated data.

## 6. UI structure (suggested, not binding)

- **Tray/menu-bar icon** with quick actions: "Refresh <each enabled schedule> now", "Open app", device status dot (awake/asleep/unreachable/battery).
- **Device page**: connection settings (LAN IP, BLE name), live `deviceInfo` card, wake/sleep/reboot/clear buttons, settings editor.
- **Gallery page**: paginated grid of gallery images with thumbnails (device serves no thumbnails — either skip previews or show name/size/date), delete, "show now", playlist editor.
- **Widgets page**: one card per widget with its config form (symbols/range; lat/lon/city; date/title/background query or photo), orientation + rotate, a **Preview** button (renders to the offscreen webview and shows the JPEG), and **Push now**.
- **Schedules page**: CRUD list — each schedule = widget + its config + cron expression (with a friendly interval picker that compiles to cron) + enabled toggle; per-schedule last-run status and a run-history log view.

## 7. Milestones & acceptance criteria

**M1 — Device client core (Rust lib, no UI polish)**
- `DeviceClient` implementing: wake-if-needed (BLE pulse per §4.1.3–4), `info`, `state`, `wait_ready`, `upload_and_show` (fresh filename + Ready poll + display verification), `cleanup(prefix, keep)` with the empty-keep guard, gallery/playlist/power/settings calls.
- Acceptance: against a real device — push a JPEG twice in a row and prove via `deviceInfo.image` that both refreshes actually displayed; kill the device to sleep and prove one call transparently wakes it.
- Also ship a `MockDevice` (tiny HTTP server reproducing the endpoints *including* the filename-cache bug) for CI tests.

**M2 — Widget rendering pipeline**
- Offscreen webview render of all three templates at 1200×1600 and 1600×1200(+rotation), pixel-identical layout to the Python/Playwright output within reason (spot-check: no clipped forecast row, round endpoint dot on sparklines, correct gradient theme per condition).
- Acceptance: golden-image snapshot tests at both orientations for each widget with fixed mock data.

**M3 — UI: device + gallery + widgets pages with preview/push.**
- Acceptance: a user with only the LAN IP can go from first launch → see device info → preview weather → push it, in under a minute, no docs.

**M4 — Scheduler + tray**
- Cron-driven background runs with: overlap prevention (skip if the same schedule is still running), retry once after 60 s on failure, run history persisted, failure notification (OS notification + tray badge).
- Acceptance: schedule weather every 2 min, close the window (app stays in tray), observe 3 consecutive on-time refreshes on the physical panel; unplug network, observe a failed run recorded + notified, plug back, observe recovery.

**M5 — Packaging**: signed macOS build (Bluetooth + notification entitlements/usage strings), auto-launch-at-login option.

## 8. Risks / open questions for the implementer

- Webview capture fidelity (fonts, `container-query` support, `devicePixelRatio`) is the main technical risk — validate M2 first, before investing in UI. If WKWebView capture proves unreliable, fall back to `headless_chrome`.
- System fonts differ per OS: the templates use font stacks that assume macOS (Avenir Next, Didot). Bundle open substitutes (e.g. Nunito Sans, Playfair Display) with the app so output is consistent cross-platform.
- BLE on Windows/Linux is less battle-tested for this device; keep wake behind the same "poll HTTP decides success" contract so partial BLE support degrades gracefully (user can wake via the vendor app and everything else still works).
