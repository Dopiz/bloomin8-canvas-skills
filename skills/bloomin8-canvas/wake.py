"""BLE wake pulse for Bloomin8 Canvas.

Usage:
    uv run --with bleak python wake.py            # discover by name "Bloomin8"
    uv run --with bleak python wake.py <mac|name> # explicit identifier

Lookup strategy:
    1. If arg matches MAC (XX:XX:XX:XX:XX:XX), try BleakScanner.discover() then
       match by .address (works on Linux/Windows; macOS hides hardware MAC).
    2. Otherwise treat as name substring (case-insensitive).
    3. Falls back to env BLOOMIN8_BLE_NAME (default "Bloomin8") then BLOOMIN8_BLE_MAC.

Protocol (from ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component
custom_components/bloomin8_eink_canvas/ble_wake.py):
    GATT char:    0000f001-0000-1000-8000-00805f9b34fb  (write-without-response)
    Pulse:        write b'\\x01' -> sleep 1ms -> write b'\\x00' -> disconnect

After this exits 0, sleep ~4 seconds before polling /deviceInfo.
"""

import asyncio
import os
import re
import sys
import time

from bleak import BleakClient, BleakScanner

CHAR_UUID = "0000f001-0000-1000-8000-00805f9b34fb"
# Empirically: the 1ms gap from the HA reference impl is too short for firmware
# 1.8.35 to bring up Wi-Fi. 500ms (mimicking a real button hold) wakes reliably.
PULSE_GAP_S = 0.5
SCAN_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 8.0
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


async def find_device(ident: str):
    is_mac = bool(MAC_RE.match(ident))
    print(f"[scan] {SCAN_TIMEOUT_S}s, matching by {'MAC' if is_mac else 'name'} '{ident}'...", flush=True)
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_S)
    ident_l = ident.lower()
    for d in devices:
        if is_mac and d.address.lower() == ident_l:
            return d
        if not is_mac and d.name and ident_l in d.name.lower():
            return d
    return None


async def wake(ident: str) -> int:
    t0 = time.monotonic()
    device = await find_device(ident)
    if device is None:
        print("[scan] FAILED — device not found. Out of range, advertising off, or Bluetooth permission missing.", file=sys.stderr)
        return 2
    print(f"[scan] found name={device.name!r} addr={device.address} in {time.monotonic()-t0:.2f}s", flush=True)

    t_connect = time.monotonic()
    async with BleakClient(device, timeout=CONNECT_TIMEOUT_S) as client:
        print(f"[connect] OK in {time.monotonic()-t_connect:.2f}s", flush=True)
        await client.write_gatt_char(CHAR_UUID, b"\x01", response=False)
        await asyncio.sleep(PULSE_GAP_S)
        await client.write_gatt_char(CHAR_UUID, b"\x00", response=False)
        print(f"[pulse] sent 0x01->0x00 to {CHAR_UUID}", flush=True)

    print(f"[done] total {time.monotonic()-t0:.2f}s — wait ~4s then poll /deviceInfo", flush=True)
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        ident = sys.argv[1]
    else:
        ident = os.environ.get("BLOOMIN8_BLE_NAME") or os.environ.get("BLOOMIN8_BLE_MAC") or "Bloomin8"
    try:
        return asyncio.run(wake(ident))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
