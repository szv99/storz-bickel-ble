"""Command line: ``storz-bickel-ble scan | info | monitor``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict

from . import VaporizerClient, discover, find_device, monitor
from .models import VaporizerState


def _fmt(s: VaporizerState) -> str:
    def v(x, unit=""):
        return "-" if x is None else f"{x:g}{unit}" if isinstance(x, float) else f"{x}{unit}"

    parts = [
        time.strftime("%H:%M:%S"),
        f"{s.family.display_name if s.family else '?'}",
        f"heater={s.heater_mode.name}",
        f"temp={v(s.current_temp, '°C')}/{v(s.target_temp, '°C')}",
        f"reached={'yes' if s.setpoint_reached else 'no'}",
        f"battery={v(s.battery, '%')}",
    ]
    if s.charging:
        parts.append("charging")
    if s.auto_shutoff_s is not None:
        parts.append(f"auto-off={s.auto_shutoff_s}s")
    if s.pump_on is not None:
        parts.append(f"pump={'on' if s.pump_on else 'off'}")
    if s.heater_on and s.family and s.family.value in ("venty", "veazy"):
        parts.append(f"puffs={s.puffs}{' PUFF' if s.puffing else ''}")
    return "  ".join(parts)


def _json(s: VaporizerState) -> str:
    d = asdict(s)
    d.pop("raw", None)
    d["family"] = s.family.value if s.family else None
    d["heater_mode"] = s.heater_mode.name.lower()
    return json.dumps(d, ensure_ascii=False)


async def _scan(args) -> int:
    devices = await discover(timeout=args.timeout)
    for dev, family in devices:
        print(f"{dev.address}  {dev.name}  ({family.display_name})")
    if not devices:
        print("no Storz & Bickel devices found", file=sys.stderr)
        return 1
    return 0


async def _info(args) -> int:
    dev = await find_device(args.name, address=args.address, timeout=args.timeout)
    if dev is None:
        print("device not found", file=sys.stderr)
        return 1
    async with VaporizerClient(dev) as vap:
        s = vap.state
    print(_json(s) if args.json else "\n".join(
        f"{k}: {val}" for k, val in json.loads(_json(s)).items()))
    return 0


async def _monitor(args) -> int:
    async for s in monitor(args.name, address=args.address, scan_timeout=args.timeout):
        if s is None:
            print(f"{time.strftime('%H:%M:%S')}  offline / searching...", flush=True)
        else:
            print(_json(s) if args.json else _fmt(s), flush=True)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="storz-bickel-ble",
                                 description="Read Storz & Bickel vaporizers over Bluetooth LE.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("scan", "list nearby devices"),
                        ("info", "connect once and print the full state"),
                        ("monitor", "follow the state, reconnecting forever")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--timeout", type=float, default=10.0, help="scan timeout (s)")
        if name != "scan":
            p.add_argument("--name", help="substring of the BLE name, e.g. VY123456")
            p.add_argument("--address", help="BLE address / CoreBluetooth UUID")
            p.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()
    run = {"scan": _scan, "info": _info, "monitor": _monitor}[args.cmd]
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
