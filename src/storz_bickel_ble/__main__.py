"""Command line: ``storz-bickel-ble <command>``; run with ``--help`` for the list."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict

from . import VaporizerError, connect, discover, monitor
from .models import HeaterMode, VaporizerState


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


def _on_off(value: str) -> bool:
    v = value.lower()
    if v in ("on", "1", "true", "yes"):
        return True
    if v in ("off", "0", "false", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected on/off, got {value!r}")


# command -> (help, argument spec or None, action(vap, value), state predicate to wait for)
CONTROLS = {
    "heat": ("heater: on, off, boost or superboost (boost modes: Venty/Veazy)",
             dict(choices=["on", "off", "boost", "superboost"]),
             lambda vap, v: vap.set_heater_mode(HeaterMode[v.upper()]),
             lambda s, v: s.heater_mode.name.lower() == v),
    "temp": ("set the target temperature in °C",
             dict(type=float, metavar="CELSIUS"),
             lambda vap, v: vap.set_temperature(v),
             lambda s, v: s.target_temp == round(v, 1)),
    "boost-offset": ("set the boost offset in °C",
                     dict(type=int, metavar="CELSIUS"),
                     lambda vap, v: vap.set_boost_offset(v),
                     lambda s, v: s.boost_offset == v),
    "superboost-offset": ("set the superboost offset in °C (Venty/Veazy)",
                          dict(type=int, metavar="CELSIUS"),
                          lambda vap, v: vap.set_superboost_offset(v),
                          lambda s, v: s.superboost_offset == v),
    "pump": ("air pump on/off (Volcano)",
             dict(type=_on_off, metavar="on|off"),
             lambda vap, v: vap.set_pump(v),
             lambda s, v: s.pump_on == v),
    "brightness": ("display brightness, 0-100 percent",
                   dict(type=int, metavar="PERCENT"),
                   lambda vap, v: vap.set_brightness(v),
                   None),
    "vibration": ("vibration on/off",
                  dict(type=_on_off, metavar="on|off"),
                  lambda vap, v: vap.set_vibration(v),
                  lambda s, v: s.vibration == v),
    "auto-off": ("auto shut-off time in seconds (Crafty, Volcano)",
                 dict(type=int, metavar="SECONDS"),
                 lambda vap, v: vap.set_auto_off(v),
                 lambda s, v: s.auto_off_setting_s == v),
    "unit": ("unit shown on the device: C or F",
             dict(choices=["C", "F", "c", "f"]),
             lambda vap, v: vap.set_unit(v),
             lambda s, v: s.fahrenheit == (v.upper() == "F")),
    "locate": ("make the device vibrate / blink (Venty, Veazy, Crafty)",
               None,
               lambda vap, v: vap.locate(),
               None),
}


async def _scan(args) -> int:
    devices = await discover(timeout=args.timeout)
    for dev, family in devices:
        print(f"{dev.address}  {dev.name}  ({family.display_name})")
    if not devices:
        print("no Storz & Bickel devices found", file=sys.stderr)
        return 1
    return 0


async def _info(args) -> int:
    async with connect(args.name, address=args.address, timeout=args.timeout) as vap:
        s = vap.state
        features = sorted(vap.features)
    if args.json:
        print(_json(s))
    else:
        for k, val in json.loads(_json(s)).items():
            print(f"{k}: {val}")
        print(f"controls: {', '.join(features)}")
    return 0


async def _monitor(args) -> int:
    async for s in monitor(args.name, address=args.address, scan_timeout=args.timeout):
        if s is None:
            print(f"{time.strftime('%H:%M:%S')}  offline / searching...", flush=True)
        else:
            print(_json(s) if args.json else _fmt(s), flush=True)
    return 0


async def _control(args) -> int:
    _help, _spec, action, done = CONTROLS[args.cmd]
    value = getattr(args, "value", None)
    async with connect(args.name, address=args.address, timeout=args.timeout) as vap:
        await action(vap, value)
        state = vap.state
        try:
            if done:
                state = await vap.wait_for(lambda s: done(s, value), timeout=5)
            else:
                await asyncio.sleep(1)
                state = vap.state
        except asyncio.TimeoutError:
            print("command sent, but the device has not confirmed it yet", file=sys.stderr)
            state = vap.state
    print(_json(state) if args.json else _fmt(state))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="storz-bickel-ble",
        description="Monitor and control Storz & Bickel vaporizers over Bluetooth LE.")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, help_, spec=None, select=True):
        p = sub.add_parser(name, help=help_, description=help_)
        if spec is not None:
            p.add_argument("value", **spec)
        p.add_argument("--timeout", type=float, default=10.0, help="scan timeout (s)")
        if select:
            p.add_argument("--name", help="substring of the BLE name, e.g. VY123456")
            p.add_argument("--address", help="BLE address / CoreBluetooth UUID")
            p.add_argument("--json", action="store_true", help="JSON output")

    add("scan", "list nearby devices", select=False)
    add("info", "connect once and print the full state")
    add("monitor", "follow the state, reconnecting forever")
    for name, (help_, spec, _a, _d) in CONTROLS.items():
        add(name, help_, spec)

    args = ap.parse_args()
    run = {"scan": _scan, "info": _info, "monitor": _monitor}.get(args.cmd, _control)
    try:
        sys.exit(asyncio.run(run(args)))
    except (VaporizerError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
