# storz-bickel-ble

Unofficial async Python library to **monitor and control** Storz & Bickel vaporizers
over Bluetooth LE. Built on [bleak](https://github.com/hbldh/bleak), so it runs on
macOS, Linux and Windows.

- **Monitor:** read temperatures, heater mode, battery, charging, auto-off timer,
  brightness, vibration, usage counters, serial and firmware. On Venty/Veazy it also
  counts **puffs**.
- **Control:** heater on/off, target temperature, boost offsets, Volcano air pump,
  brightness, vibration, auto-off time, display unit and find-my.
- **Easy to call:** one `connect()` / `monitor()` call, plain `await vap.heater_on()`
  methods, clear errors, and a CLI for everything.

> Not affiliated with, endorsed by or supported by Storz & Bickel GmbH. "Storz & Bickel",
> "Venty", "Veazy", "Crafty", "Mighty" and "Volcano" are trademarks of their owners.
> The protocol was documented by observing the device and the behaviour of the official
> web app, for interoperability.

> **Safety:** heater commands switch on a real heating element. Don't start heating a
> device you are not attending.

## Supported devices

| Device | Protocol | Status |
|---|---|---|
| Venty | single characteristic, request/response | verified on hardware (fw V01.11) |
| Veazy | same as Venty | expected to work, untested |
| Crafty+ / Mighty+ | one GATT characteristic per value | ported, untested on hardware |
| Volcano Hybrid | one GATT characteristic per value | ported, untested on hardware |

Reports (working or not) from Crafty+, Mighty+, Veazy and Volcano owners are very welcome.
`storz-bickel-ble info --json` output is the most useful thing to attach.

### What each device can do

| Control | Venty / Veazy | Crafty+ / Mighty+ | Volcano Hybrid |
|---|---|---|---|
| heater on / off | yes | yes | yes |
| boost / superboost mode | yes¹ | — (button only) | — |
| target temperature | 40–210 °C | 40–210 °C | 40–230 °C |
| boost offset | yes | yes | — |
| superboost offset | yes | — (always boost + 15 °C) | — |
| air pump | — | — | yes |
| brightness | yes (9 levels) | yes | yes |
| vibration | yes | yes | yes |
| auto-off time | — (fixed) | 10–300 s | 60–21600 s |
| display unit °C/°F | yes | — | yes |
| locate (find-my) | yes | yes | — |

¹ The official app only switches the Venty heater on and off. Boost and superboost over
BLE write the same mode byte the device reports, and are experimental.

Verified on a Venty so far: target temperature, brightness, vibration and all reading.
Call `vap.supports("pump")` (or check `vap.features`) to test for a feature at runtime.

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

storz-bickel-ble temp 185                  # target temperature
storz-bickel-ble heat on                   # on | off | boost | superboost
storz-bickel-ble pump on                   # Volcano
storz-bickel-ble boost-offset 15
storz-bickel-ble superboost-offset 30
storz-bickel-ble brightness 60             # percent
storz-bickel-ble vibration off
storz-bickel-ble auto-off 120              # seconds
storz-bickel-ble unit F
storz-bickel-ble locate
```

Every command accepts `--name`, `--address`, `--timeout` and `--json`. Control commands
wait until the device confirms the change, then print the new state:

```
22:22:41  Venty  heater=ON  temp=-/210°C  reached=yes  battery=31%  auto-off=119s  puffs=2 PUFF
```

## Python

### Control

```python
import asyncio
from storz_bickel_ble import connect

async def main():
    async with connect() as vap:                 # first device found; or connect("VY123456")
        await vap.set_temperature(185)
        await vap.heater_on()
        state = await vap.wait_for(lambda s: s.setpoint_reached, timeout=300)
        print("ready at", state.target_temp, "°C, battery", state.battery, "%")

asyncio.run(main())
```

All control methods are coroutines on the connected client:

| Method | Notes |
|---|---|
| `heater_on()`, `heater_off()`, `set_heater(on)` | all devices |
| `boost()`, `superboost()`, `set_heater_mode(HeaterMode.X)` | Venty / Veazy |
| `set_temperature(celsius)` | base target; boost offsets are added on top |
| `set_boost_offset(c)`, `set_superboost_offset(c)` | offsets in °C |
| `pump_on()`, `pump_off()`, `set_pump(on)` | Volcano |
| `set_brightness(percent)` | 0–100 on every device |
| `set_vibration(on)` | |
| `set_auto_off(seconds)` | Crafty, Volcano |
| `set_unit("C" \| "F")` | display unit |
| `locate()` | device vibrates / blinks |
| `wait_for(predicate, timeout)` | wait until `predicate(state)` is true |

Errors:
- `UnsupportedOperation`: the device can't do that, e.g. `pump_on()` on a Venty.
- `ValueError`: the value is out of range, with the allowed range in the message.
- `VaporizerDisconnected`: the link is gone.

All of them except `ValueError` derive from `VaporizerError`.

### Monitor

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

Within one connection you can stream changes, or register callbacks:

```python
async with connect() as vap:
    print(vap.state)                       # filled in before connect() returns
    remove = vap.add_listener(lambda state: print(state.battery))
    async for state in vap.updates():      # current state, then every change
        ...                                # raises VaporizerDisconnected on link loss
```

For full control over discovery, use `find_device()` / `discover()` and
`VaporizerClient(device)`.

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
| `auto_off_setting_s` | configured auto-off time (Crafty, Volcano) |
| `brightness`, `vibration` | display brightness in percent, vibration enabled |
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

Writes use the same 20-byte packets, with a field mask in byte 1.

`0x01` write masks:
- `0x02`: target temperature in `[4..5]`
- `0x04`: boost offset in `[6]`
- `0x08`: superboost offset in `[7]`
- `0x20`: heater mode in `[11]`
- `0x80`: settings, with values in `[14]` and the mask of bits to change in `[15]`

`0x06` write masks (7-byte packet):
- `0x01`: brightness 1-9 in `[2]`
- `0x08`: vibration in `[5]`

Find-my is `0x0D` with `[1] = 1`.

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
| `01c3` | status 2 (notify, read-modify-write): bit0 vibration off, bit2 setpoint reached, bit3 find-my |
| `0051` | brightness 0-100 |
| `0061` | auto-off time s (write `815` to `01b3` first) |
| `0081` / `0091` | heater on / off (write 2 zero bytes) |

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
| `1010000e` | register 3: bit10 vibration off |
| `10110005` | brightness 0-100 |
| `1011000d` | auto-off time s |
| `1011000f` / `10110010` | heater on / off (write 1 zero byte) |
| `10110013` / `10110014` | pump on / off (write 1 zero byte) |

Writing the target temperature uses a u32 (it reads back as u16). Registers 2 and 3 are
written as a u32. The low 16 bits are the bit mask to change; bit 16 is 1 to set those bits
and 0 to clear them.

There is no "reached" flag on the Volcano. The library treats the setpoint as reached
within ±1 °C of the target, the same rule the official app uses.

## License

MIT
