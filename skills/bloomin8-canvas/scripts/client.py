# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Canonical CLI/module for all Bloomin8 Canvas LAN operations.

Every device operation goes through here so the firmware gotchas live in one
place instead of being re-implemented per skill or curl chain:

- wake-then-poll when the device is asleep (BLE pulse via wake.py)
- upload under a FRESH filename every time — firmware 1.8.35 caches the
  rendered image by file path, so reusing a filename (even after deleting it
  first) redisplays stale content while /state still reports 100
- poll /state to Ready and verify /deviceInfo.image actually points at the
  new file, so a silently-stale display is never mistaken for success
- delete old same-prefix files so the gallery doesn't grow unbounded

Usable as a CLI (`uv run client.py <cmd> ...`) or imported as a module.
Run `uv run client.py --help` for the full command list.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests


def _lan_ip() -> str:
    ip = os.environ.get("BLOOMIN8_LAN_IP")
    if not ip:
        raise SystemExit("BLOOMIN8_LAN_IP not set")
    return ip


def _request(method: str, path: str, *, params: dict | None = None, json_body=None, files=None, timeout: float = 15):
    r = requests.request(
        method, f"http://{_lan_ip()}{path}", params=params, json=json_body, files=files, timeout=timeout
    )
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"ok": True, "text": r.text.strip()}


def device_info(timeout: float = 3) -> dict:
    return requests.get(f"http://{_lan_ip()}/deviceInfo", timeout=timeout).json()


def is_awake(timeout: float = 3) -> bool:
    try:
        device_info(timeout=timeout)
        return True
    except Exception:
        return False


def wake(max_wait: int = 30) -> bool:
    """BLE-wake the device and poll until /deviceInfo responds, or already awake."""
    if is_awake():
        return True
    wake_py = next(Path.home().glob(".claude/skills/*/wake.py"), None)
    if not wake_py:
        raise SystemExit("wake.py not found under ~/.claude/skills — is the bloomin8-canvas skill installed?")
    # Not check=True: on some setups wake.py's BLE stack (bleak/CoreBluetooth)
    # throws during post-pulse disconnect cleanup and exits non-zero even
    # though the wake pulse itself was already sent successfully. The real
    # success signal is whether the device answers HTTP afterward, so we log
    # a non-zero exit but still poll rather than aborting on a false negative.
    result = subprocess.run(["uv", "run", "--with", "bleak", "python", str(wake_py)])
    if result.returncode != 0:
        print(f"warn: wake.py exited {result.returncode}; polling anyway in case the pulse still landed", file=sys.stderr)
    for _ in range(max_wait):
        if is_awake(timeout=2):
            return True
        time.sleep(1)
    return False


def wake_if_needed(max_wait: int = 30) -> None:
    if not is_awake() and not wake(max_wait=max_wait):
        raise SystemExit(f"device did not wake within {max_wait}s")


def state() -> dict:
    return _request("GET", "/state", timeout=5)


def wait_ready(timeout: float = 60, interval: float = 2) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = state()
        if last.get("status") == 100:
            return last
        time.sleep(interval)
    raise SystemExit(f"device did not reach Ready within {timeout}s (last state: {last})")


def upload_and_show(path: str, gallery: str = "default", prefix: str = "frame", show_now: bool = True) -> dict:
    """Upload under a fresh timestamped filename and verify the frame actually
    displays it. Never pass a fixed filename in — see the module docstring."""
    wake_if_needed()
    filename = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    with open(path, "rb") as f:
        resp = _request(
            "POST", "/upload",
            params={"filename": filename, "gallery": gallery, "show_now": 1 if show_now else 0},
            files={"image": f}, timeout=30,
        )
    if show_now:
        wait_ready()
        shown = device_info().get("image", "")
        if not shown.endswith(filename):
            raise SystemExit(f"upload reported success but the device is showing '{shown}', not '{filename}' — check for a firmware caching issue")
    return {"filename": filename, "gallery": gallery, "upload_response": resp}


def cleanup_old(prefix: str, keep: str, gallery: str = "default") -> list:
    """Delete gallery images whose name starts with `prefix`, except `keep`."""
    # Guard against `--keep "$FN"` where FN came from a FAILED upload (empty
    # string): without this, cleanup would delete every same-prefix image,
    # including the one currently on the panel.
    if not keep.strip():
        raise SystemExit("cleanup: --keep is empty (did the preceding upload fail?); refusing to delete everything")
    listing = _request("GET", "/gallery", params={"gallery_name": gallery, "offset": 0, "limit": 200})
    deleted = []
    for item in listing.get("data", []):
        name = item.get("name", "")
        if name.startswith(prefix) and name != keep:
            _request("POST", "/image/delete", params={"image": name, "gallery": gallery})
            deleted.append(name)
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # -- status / power ------------------------------------------------------
    sub.add_parser("wake", help="BLE-wake the device if asleep, poll until reachable")
    sub.add_parser("info", help="Print /deviceInfo (wakes the device if asleep)")
    sub.add_parser("state", help="Print /state")
    sub.add_parser("wait-ready", help="Poll /state until status 100")
    sub.add_parser("whistle", help="Keep-alive ping that postpones sleep")
    sub.add_parser("sleep", help="Put the device to sleep")
    sub.add_parser("reboot", help="Reboot the device")
    sub.add_parser("clear-screen", help="Clear the panel to white")
    st = sub.add_parser("settings", help="Update device settings from a JSON object")
    st.add_argument("json", help='e.g. \'{"max_idle":300,"sleep_duration":86400}\'')

    # -- content -------------------------------------------------------------
    up = sub.add_parser("upload", help="Upload + display a JPEG under a fresh timestamped filename")
    up.add_argument("path")
    up.add_argument("--gallery", default="default")
    up.add_argument("--prefix", default="frame", help="Filename prefix; a timestamp is appended")
    up.add_argument("--no-show", action="store_true", help="Upload without displaying immediately")

    cl = sub.add_parser("cleanup", help="Delete old same-prefix images, keeping one")
    cl.add_argument("--prefix", required=True)
    cl.add_argument("--keep", required=True, help="Filename to keep (e.g. the one just uploaded)")
    cl.add_argument("--gallery", default="default")

    de = sub.add_parser("image-delete", help="Delete a single image from a gallery")
    de.add_argument("image")
    de.add_argument("--gallery", default="default")

    # -- display / playback --------------------------------------------------
    si = sub.add_parser("show-image", help="Display an image already on the device")
    si.add_argument("path", help="Device path, e.g. /gallerys/default/f1.jpg")
    sg = sub.add_parser("show-gallery", help="Start a gallery slideshow")
    sg.add_argument("name")
    sg.add_argument("--duration", type=int, default=120, help="Seconds per image")
    sp = sub.add_parser("show-playlist", help="Start a playlist")
    sp.add_argument("name")
    sub.add_parser("show-next", help="Skip to the next item in the current queue")

    # -- galleries -----------------------------------------------------------
    sub.add_parser("gallery-list", help="List all galleries")
    gc = sub.add_parser("gallery-create", help="Create an empty gallery")
    gc.add_argument("name")
    gd = sub.add_parser("gallery-delete", help="Delete a gallery AND every image inside it")
    gd.add_argument("name")
    gi = sub.add_parser("gallery-images", help="List images in a gallery")
    gi.add_argument("name")
    gi.add_argument("--offset", type=int, default=0)
    gi.add_argument("--limit", type=int, default=50)

    # -- playlists -----------------------------------------------------------
    sub.add_parser("playlist-list", help="List playlists")
    pg = sub.add_parser("playlist-get", help="Print a playlist's content")
    pg.add_argument("name")
    ps = sub.add_parser("playlist-set", help="Create/overwrite a playlist from a JSON object")
    ps.add_argument("json", help='{"name":...,"type":"duration","list":[{"name":...,"duration":40,"time":""}]}')
    pd = sub.add_parser("playlist-delete", help="Delete a playlist")
    pd.add_argument("name")

    args = ap.parse_args()

    # state/wait-ready must not trigger a wake: they are used to observe the
    # device, including while it is falling asleep. Everything else needs it up.
    if args.cmd not in ("state", "wait-ready", "wake"):
        wake_if_needed()

    if args.cmd == "wake":
        out = {"awake": wake()}
    elif args.cmd == "info":
        out = device_info()
    elif args.cmd == "state":
        out = state()
    elif args.cmd == "wait-ready":
        out = wait_ready()
    elif args.cmd == "whistle":
        out = _request("GET", "/whistle")
    elif args.cmd == "sleep":
        out = _request("POST", "/sleep")
    elif args.cmd == "reboot":
        out = _request("POST", "/reboot")
    elif args.cmd == "clear-screen":
        out = _request("POST", "/clearScreen")
    elif args.cmd == "settings":
        out = _request("POST", "/settings", json_body=json.loads(args.json))
    elif args.cmd == "upload":
        out = upload_and_show(args.path, gallery=args.gallery, prefix=args.prefix, show_now=not args.no_show)
    elif args.cmd == "cleanup":
        out = {"deleted": cleanup_old(args.prefix, args.keep, gallery=args.gallery)}
    elif args.cmd == "image-delete":
        out = _request("POST", "/image/delete", params={"image": args.image, "gallery": args.gallery})
    elif args.cmd == "show-image":
        out = _request("POST", "/show", json_body={"play_type": 0, "image": args.path})
    elif args.cmd == "show-gallery":
        out = _request("POST", "/show", json_body={"play_type": 1, "gallery": args.name, "duration": args.duration})
    elif args.cmd == "show-playlist":
        out = _request("POST", "/show", json_body={"play_type": 2, "playlist": args.name})
    elif args.cmd == "show-next":
        out = _request("POST", "/showNext")
    elif args.cmd == "gallery-list":
        out = _request("GET", "/gallery/list")
    elif args.cmd == "gallery-create":
        out = _request("PUT", "/gallery", params={"name": args.name})
    elif args.cmd == "gallery-delete":
        out = _request("DELETE", "/gallery", params={"name": args.name})
    elif args.cmd == "gallery-images":
        out = _request("GET", "/gallery", params={"gallery_name": args.name, "offset": args.offset, "limit": args.limit})
    elif args.cmd == "playlist-list":
        out = _request("GET", "/playlist/list")
    elif args.cmd == "playlist-get":
        out = _request("GET", "/playlist", params={"name": args.name})
    elif args.cmd == "playlist-set":
        out = _request("PUT", "/playlist", json_body=json.loads(args.json))
    elif args.cmd == "playlist-delete":
        out = _request("DELETE", "/playlist", params={"name": args.name})

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
