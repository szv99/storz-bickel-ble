# storz-bickel-ble

Unofficial async Python library for reading Storz & Bickel vaporizers over Bluetooth LE,
built on [bleak](https://github.com/hbldh/bleak) (macOS, Linux, Windows).

It is **read-only**: it reads temperatures, heater mode, battery, auto-off timer, usage
counters and serial/firmware. On Venty/Veazy it also counts **puffs**. It never sends
heating or settings commands.

> Not affiliated with, endorsed by or supported by Storz & Bickel GmbH. "Storz & Bickel",
> "Venty", "Veazy", "Crafty", "Mighty" and "Volcano" are trademarks of their owners.
> The protocol was documented by observing the device and the behaviour of the official
> web app, for interoperability.

## Supported devices

| Device | Protocol | Status |
|---|---|---|
| Venty | single characteristic, request/response | verified on hardware (fw V01.11) |
| Veazy | same as Venty | expected to work, untested |
| Crafty+ / Mighty+ | one GATT characteristic per value | ported, untested on hardware |
| Volcano Hybrid | one GATT characteristic per value | ported, untested on hardware |

Reports (working or not) from Crafty+, Mighty+, Veazy and Volcano owners are very welcome.
`storz-bickel-ble info --json` output is the most useful thing to attach.

## Install

```bash
pip install git+https://github.com/szv99/storz-bickel-ble
```

Python 3.9+. The only dependency is `bleak`.

## Command line

```bash
storz-bickel-ble scan                      # list nearby devices
storz-bickel-ble info                      # connect once, print everything
storz-bickel-ble monitor                   # follow the state, reconnecting forever
storz-bickel-ble monitor --name VY123456 --json
```

```
22:22:41  Venty  heater=ON  temp=-/210°C  reached=yes  battery=31%  auto-off=119s  puffs=2 PUFF
```

## Python

Follow a device forever, with automatic reconnects:

```python
import asyncio
from storz_bickel_ble import monitor

async def main():
    async for state in monitor():          # optional: monitor("VY123456")
        if state is None:
            print("offline")               # yielded once per outage
        else:
            print(state.heater_mode.name, state.target_temp, state.battery, state.puffs)

asyncio.run(main())
```

Single connection, full control over the lifecycle:

```python
from storz_bickel_ble import VaporizerClient, VaporizerDisconnected, find_device

device = await find_device()               # first S&B device seen, or None
async with VaporizerClient(device) as vap:
    print(vap.state)                       # filled in before connect() returns
    try:
        async for state in vap.updates():  # current state, then every change
            ...
    except VaporizerDisconnected:
        ...
```

Callbacks instead of iteration: `remove = vap.add_listener(lambda state: ...)`.

### `VaporizerState`

| Field | Meaning |
|---|---|
| `family` | `DeviceFamily.VENTY / VEAZY / CRAFTY / VOLCANO` |
| `name`, `serial`, `firmware` | identity |
| `current_temp`, `target_temp` | °C (always Celsius; `fahrenheit` = the device's display unit) |
| `boost_offset`, `superboost_offset` | °C added to the target in boost / superboost |
| `effective_target_temp` | target including the active boost offset |
| `heater_mode`, `heater_on`, `boost`, `superboost` | `HeaterMode.OFF / ON / BOOST / SUPERBOOST` |
| `setpoint_reached` | heater is at temperature |
| `auto_shutoff_s` | seconds until auto-off |
| `pump_on` | Volcano only |
| `battery`, `charging` | percent, charger connected (Venty) |
| `heater_runtime_min`, `charging_time_min` | lifetime counters |
| `puffs`, `puffing` | Venty/Veazy: puffs in this heating session, draw in progress |
| `updated`, `age` | timestamp of the last update, seconds since |
| `raw` | raw packets / registers for debugging |

Fields a device does not report are `None`. The Venty (fw V01.11) does not report the
current temperature, so `current_temp` is `None` there.

## Puff detection (Venty / Veazy)

At temperature, the Venty counts its auto-off timer down once per second. When it detects
an inhalation, it resets the counter to its maximum and holds it there for as long as the
draw lasts. The library polls status every 0.5 s (like the official app). Once the setpoint
is reached, it treats an increase of the counter as the start of a puff and the next
decrease as its end.

Two cases are filtered out:
- A reset within 2 s of the previous puff's end is the same puff (a breath pause).
- Temperature and boost button presses reset the counter too, so resets in the 2 s after a
  settings change are ignored.

`PuffDetector` is exported if you want to feed it states yourself.

## Reliability notes

- Every GATT operation has a timeout. CoreBluetooth and BlueZ can otherwise hang forever
  on a half-dead link.
- On Venty/Veazy, a single unanswered poll is tolerated. Four in a row raise
  `VaporizerDisconnected`.
- On Crafty and Volcano a register is read every 5 s as a liveness check, because the
  values are notification-driven and may stay silent.
- Each device accepts one BLE connection at a time, so close the official app first.
- macOS asks for Bluetooth permission for the app that runs Python (Terminal, iTerm, …). A
  bare `python` started by launchd is denied without a prompt. To run as a LaunchAgent,
  wrap it in a small `.app` bundle with `NSBluetoothAlwaysUsageDescription` in its
  `Info.plist`.

## Protocol reference

All multi-byte values are little-endian. Temperatures are tenths of °C.

### Venty / Veazy

- **Service:** `00000000-5354-4f52-5a26-4249434b454c`.
- **Characteristic:** `00000001-5354-4f52-5a26-4249434b454c` (write + notify).
- **Requests:** 20 bytes, zero-filled, with the command in byte 0. The answer is a
  notification that starts with the same command byte.

| Cmd | Response layout |
|---|---|
| `0x01` status | `[2..3]` current temp (`0x8000` = n/a), `[4..5]` target, `[6]` boost offset °C, `[7]` superboost offset °C, `[8]` battery %, `[9]+[10]` auto-off s, `[11]` heater mode 0 off / 1 on / 2 boost / 3 superboost, `[13]` charger, `[14]` flags (bit0 °F display, bit1 setpoint reached, bit3 eco charge, bit5 eco voltage, bit6 boost visualization), `[16]` bit0 BLE always on |
| `0x02` version | `[1]` flags (bit0 application running), `[2..7]` firmware ASCII, `[11..16]` bootloader ASCII |
| `0x04` usage | `[1..3]` heater runtime min (u24), `[4..6]` charging time min (u24) |
| `0x05` identity | serial = ASCII `[15..16]` + `[9..14]`; `[18]` Veazy colour |
| `0x06` settings (7-byte request) | `[2]` brightness 1-9, `[5]` vibration, `[6]` boost timeout disabled |

Writes use `0x01` with a field mask in byte 1:
- `0x02`: target temperature
- `0x04`: boost offset
- `0x08`: superboost offset
- `0x20`: heater
- `0x80`: settings

They are listed here for completeness only. This library does not send them.

### Crafty+ / Mighty+

- **UUIDs:** `0000XXXX-4c45-4b43-4942-265a524f5453`.
- **Services:** `0001` control, `0002` info, `0003` status.

| Char | Value |
|---|---|
| `0011` | current temp (notify) |
| `0021` | target temp |
| `0031` | boost offset |
| `0041` | battery % (notify) |
| `0071` | auto-off seconds left (notify) |
| `0032` | firmware ASCII |
| `0052` | serial ASCII |
| `0023` / `01e3` | use hours / minutes |
| `0093` | status 1 (notify on newer fw): bit4 heater, bit5 boost, bit6 superboost |
| `01c3` | status 2 (notify): bit2 setpoint reached |

Superboost is not reported. The official app shows it as target + boost + 15 °C.

### Volcano Hybrid

- **UUIDs:** `XXXXXXXX-5354-4f52-5a26-4249434b454c`.

| Char | Value |
|---|---|
| `10110001` | current temp (notify) |
| `10110003` | target temp (notify) |
| `1011000c` | auto-off seconds left (notify) |
| `10110015` / `10110016` | heater hours / minutes (notify) |
| `10100003` | firmware ASCII |
| `10100008` | serial ASCII |
| `1010000c` | register 1 (notify): bit5 heater, bit13 pump |
| `1010000d` | register 2 (notify): bit9 °F display |

There is no "reached" flag on the Volcano. The library treats the setpoint as reached
within ±1 °C of the target, the same rule the official app uses.

## License

MIT
