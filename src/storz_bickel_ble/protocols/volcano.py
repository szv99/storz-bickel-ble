"""Volcano Hybrid protocol: one GATT characteristic per value.

Ported from the vendor web app; not yet verified on hardware.
"""

from __future__ import annotations

import contextlib

from ..exceptions import VaporizerDisconnected
from ..models import HeaterMode
from . import base as feat
from .base import BaseProtocol, p16, p32, tenths, u16


def _uuid(n: int) -> str:
    return f"{n:08x}-5354-4f52-5a26-4249434b454c"


SERVICE_INFO = _uuid(0x10100000)
SERVICE_CONTROL = _uuid(0x10110000)

FIRMWARE = _uuid(0x10100003)        # ascii
SERIAL = _uuid(0x10100008)          # ascii, 8 chars
REGISTER1 = _uuid(0x1010000C)       # u16 bitfield, notify
REGISTER2 = _uuid(0x1010000D)       # u16 bitfield, notify; write: see register_write()
REGISTER3 = _uuid(0x1010000E)       # u16 bitfield; write: see register_write()
CURRENT_TEMP = _uuid(0x10110001)    # u16 1/10 °C, notify
TARGET_TEMP = _uuid(0x10110003)     # u16 1/10 °C, notify
AUTO_OFF_LEFT = _uuid(0x1011000C)   # u16 s, notify
HEATER_HOURS = _uuid(0x10110015)    # u16, notify
HEATER_MINUTES = _uuid(0x10110016)  # u16, notify
BRIGHTNESS = _uuid(0x10110005)      # u16 0-100
AUTO_OFF_SETTING = _uuid(0x1011000D)  # u16 s
HEATER_ON = _uuid(0x1011000F)       # write 1 zero byte
HEATER_OFF = _uuid(0x10110010)
PUMP_ON = _uuid(0x10110013)
PUMP_OFF = _uuid(0x10110014)

REG1_HEATER = 1 << 5
REG1_PUMP = 1 << 13
REG2_FAHRENHEIT = 1 << 9
REG3_VIBRATION_OFF = 1 << 10
REGISTER_SET = 1 << 16              # register writes: low 16 bits = mask, bit 16 = set/clear

REACHED_TOLERANCE = 1.0             # no "reached" flag; the vendor app uses ±1 °C
INVALID_TEMP_RAW = 65000            # ~0xFFFF = sensor value not available


def register_write(mask: int, value: bool) -> bytes:
    return p32(mask | (REGISTER_SET if value else 0))


class VolcanoProtocol(BaseProtocol):
    poll_interval = 5.0
    features = frozenset({feat.HEATER, feat.TEMPERATURE, feat.PUMP, feat.BRIGHTNESS,
                          feat.VIBRATION, feat.AUTO_OFF, feat.UNIT})
    temp_range = (40.0, 230.0)
    auto_off_range = (60, 21600)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._hours = None
        self._minutes = None

    def _derive(self) -> None:
        s = self.state
        s.setpoint_reached = (s.heater_on and s.current_temp is not None
                              and s.target_temp is not None
                              and abs(s.current_temp - s.target_temp) <= REACHED_TOLERANCE)
        if self._hours is not None:
            s.heater_runtime_min = self._hours * 60 + (self._minutes or 0)

    def _current(self, raw: int) -> None:
        self.state.current_temp = None if raw >= INVALID_TEMP_RAW else tenths(raw)

    def _target(self, raw: int) -> None:
        self.state.target_temp = tenths(raw)

    def _reg1(self, raw: int) -> None:
        self.state.heater_mode = HeaterMode.ON if raw & REG1_HEATER else HeaterMode.OFF
        self.state.pump_on = bool(raw & REG1_PUMP)
        self.state.raw["register1"] = raw

    def _reg2(self, raw: int) -> None:
        self.state.fahrenheit = bool(raw & REG2_FAHRENHEIT)
        self.state.raw["register2"] = raw

    def _auto_off(self, raw: int) -> None:
        self.state.auto_shutoff_s = raw

    def _set_hours(self, raw: int) -> None:
        self._hours = raw

    def _set_minutes(self, raw: int) -> None:
        self._minutes = raw

    def _notifier(self, apply):
        def cb(_char, data: bytearray) -> None:
            if len(data) >= 2:
                apply(u16(bytes(data)))
                self._derive()
                self.changed()
        return cb

    async def start(self) -> None:
        s = self.state
        with contextlib.suppress(Exception):
            s.serial = (await self.read(SERIAL)).decode("utf-8", "ignore")[:8].strip("\x00 ")
        with contextlib.suppress(Exception):
            s.firmware = (await self.read(FIRMWARE)).decode("utf-8", "ignore")[:8].strip("\x00 ")
        required = ((CURRENT_TEMP, self._current), (TARGET_TEMP, self._target),
                    (REGISTER1, self._reg1))
        optional = ((REGISTER2, self._reg2), (AUTO_OFF_LEFT, self._auto_off),
                    (HEATER_HOURS, self._set_hours), (HEATER_MINUTES, self._set_minutes))
        for uuid, apply in required:
            apply(u16(await self.read(uuid)))
            await self.notify(uuid, self._notifier(apply))
        for uuid, apply in optional:
            with contextlib.suppress(Exception):
                apply(u16(await self.read(uuid)))
                await self.notify(uuid, self._notifier(apply))
        with contextlib.suppress(Exception):
            s.brightness = u16(await self.read(BRIGHTNESS))
        with contextlib.suppress(Exception):
            s.auto_off_setting_s = u16(await self.read(AUTO_OFF_SETTING))
        with contextlib.suppress(Exception):
            s.vibration = not u16(await self.read(REGISTER3)) & REG3_VIBRATION_OFF
        self._derive()
        self.changed()

    # --- control ----------------------------------------------------------------

    async def set_heater_mode(self, mode: HeaterMode) -> None:
        await self.write(HEATER_ON if mode else HEATER_OFF, b"\x00")

    async def set_pump(self, on: bool) -> None:
        await self.write(PUMP_ON if on else PUMP_OFF, b"\x00")

    async def set_target(self, celsius: float) -> None:
        await self.write(TARGET_TEMP, p32(round(celsius * 10)))     # u32 on write, u16 on read

    async def set_brightness(self, percent: int) -> None:
        await self.write(BRIGHTNESS, p16(percent))
        self.state.brightness = percent
        self.changed()

    async def set_auto_off(self, seconds: int) -> None:
        await self.write(AUTO_OFF_SETTING, p16(seconds))
        self.state.auto_off_setting_s = seconds
        self.changed()

    async def set_fahrenheit(self, on: bool) -> None:
        await self.write(REGISTER2, register_write(REG2_FAHRENHEIT, on))

    async def set_vibration(self, on: bool) -> None:
        await self.write(REGISTER3, register_write(REG3_VIBRATION_OFF, not on))
        self.state.vibration = on
        self.changed()

    # --- lifecycle --------------------------------------------------------------

    async def poll(self) -> None:
        try:                        # liveness check while notifications are idle
            raw = u16(await self.read(REGISTER1))
        except Exception as e:
            raise VaporizerDisconnected(f"register read failed: {type(e).__name__}") from e
        self._reg1(raw)
        self._derive()
        self.changed()
