"""Volcano Hybrid protocol: one GATT characteristic per value.

Ported from the vendor web app; not yet verified on hardware.
"""

from __future__ import annotations

import contextlib

from ..exceptions import VaporizerDisconnected
from ..models import HeaterMode
from .base import BaseProtocol, tenths, u16


def _uuid(n: int) -> str:
    return f"{n:08x}-5354-4f52-5a26-4249434b454c"


SERVICE_INFO = _uuid(0x10100000)
SERVICE_CONTROL = _uuid(0x10110000)

FIRMWARE = _uuid(0x10100003)        # ascii
SERIAL = _uuid(0x10100008)          # ascii, 8 chars
REGISTER1 = _uuid(0x1010000C)       # u16 bitfield, notify
REGISTER2 = _uuid(0x1010000D)       # u16 bitfield, notify
CURRENT_TEMP = _uuid(0x10110001)    # u16 1/10 °C, notify
TARGET_TEMP = _uuid(0x10110003)     # u16 1/10 °C, notify
AUTO_OFF_LEFT = _uuid(0x1011000C)   # u16 s, notify
HEATER_HOURS = _uuid(0x10110015)    # u16, notify
HEATER_MINUTES = _uuid(0x10110016)  # u16, notify

REG1_HEATER = 1 << 5
REG1_PUMP = 1 << 13
REG2_FAHRENHEIT = 1 << 9

REACHED_TOLERANCE = 1.0             # no "reached" flag; the vendor app uses ±1 °C
INVALID_TEMP_RAW = 65000            # ~0xFFFF = sensor value not available


class VolcanoProtocol(BaseProtocol):
    poll_interval = 5.0

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
        self._derive()
        self.changed()

    async def poll(self) -> None:
        try:                        # liveness check while notifications are idle
            raw = u16(await self.read(REGISTER1))
        except Exception as e:
            raise VaporizerDisconnected(f"register read failed: {type(e).__name__}") from e
        self._reg1(raw)
        self._derive()
        self.changed()
