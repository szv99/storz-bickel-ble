"""Crafty+ / Mighty+ protocol: one GATT characteristic per value.

Ported from the vendor web app; not yet verified on hardware.
"""

from __future__ import annotations

import contextlib

from ..exceptions import VaporizerDisconnected
from ..models import HeaterMode
from .base import BaseProtocol, tenths, u16


def _uuid(n: int) -> str:
    return f"{n:08x}-4c45-4b43-4942-265a524f5453"


SERVICE_CONTROL = _uuid(0x01)
SERVICE_INFO = _uuid(0x02)
SERVICE_STATUS = _uuid(0x03)

CURRENT_TEMP = _uuid(0x11)      # u16 1/10 °C, notify
TARGET_TEMP = _uuid(0x21)       # u16 1/10 °C
BOOST_OFFSET = _uuid(0x31)      # u16 1/10 °C
BATTERY = _uuid(0x41)           # u16 %, notify
AUTO_OFF_LEFT = _uuid(0x71)     # u16 s, notify
FIRMWARE = _uuid(0x32)          # ascii
SERIAL = _uuid(0x52)            # ascii, 8 chars
USE_HOURS = _uuid(0x23)         # u16
USE_MINUTES = _uuid(0x1E3)      # u16, newer firmware only
STATUS1 = _uuid(0x93)           # u16 bitfield, notify on newer firmware
STATUS2 = _uuid(0x1C3)          # u16 bitfield, notify

STATUS1_HEATER = 1 << 4
STATUS1_BOOST = 1 << 5
STATUS1_SUPERBOOST = 1 << 6
STATUS2_SETPOINT_REACHED = 1 << 2

SUPERBOOST_EXTRA = 15           # superboost = target + boost + 15 °C (fixed in the app)


def _temp(raw: int) -> float:
    t = tenths(raw)
    if t > 210:                 # the vendor app treats >210 as °F
        t = round((t - 32) * 5 / 9, 1)
    return t


class CraftyProtocol(BaseProtocol):
    poll_interval = 5.0

    def _status1(self, raw: int) -> None:
        s = self.state
        if raw & STATUS1_SUPERBOOST:
            s.heater_mode = HeaterMode.SUPERBOOST
        elif raw & STATUS1_BOOST:
            s.heater_mode = HeaterMode.BOOST
        elif raw & STATUS1_HEATER:
            s.heater_mode = HeaterMode.ON
        else:
            s.heater_mode = HeaterMode.OFF
        if not s.heater_on:
            s.setpoint_reached = False
        s.raw["status1"] = raw

    def _status2(self, raw: int) -> None:
        self.state.setpoint_reached = self.state.heater_on and bool(raw & STATUS2_SETPOINT_REACHED)
        self.state.raw["status2"] = raw

    def _current(self, raw: int) -> None:
        self.state.current_temp = _temp(raw)

    def _battery(self, raw: int) -> None:
        self.state.battery = raw

    def _auto_off(self, raw: int) -> None:
        self.state.auto_shutoff_s = raw

    def _notifier(self, apply):
        def cb(_char, data: bytearray) -> None:
            if len(data) >= 2:
                apply(u16(bytes(data)))
                self.changed()
        return cb

    async def start(self) -> None:
        s = self.state
        with contextlib.suppress(Exception):
            s.serial = (await self.read(SERIAL)).decode("utf-8", "ignore")[:8].strip("\x00 ")
        with contextlib.suppress(Exception):
            s.firmware = (await self.read(FIRMWARE)).decode("utf-8", "ignore").strip("\x00 ")
        s.target_temp = _temp(u16(await self.read(TARGET_TEMP)))
        s.current_temp = _temp(u16(await self.read(CURRENT_TEMP)))
        s.battery = u16(await self.read(BATTERY))
        with contextlib.suppress(Exception):
            s.boost_offset = round(tenths(u16(await self.read(BOOST_OFFSET))))
            s.superboost_offset = s.boost_offset + SUPERBOOST_EXTRA
        with contextlib.suppress(Exception):
            s.auto_shutoff_s = u16(await self.read(AUTO_OFF_LEFT))
        with contextlib.suppress(Exception):
            minutes = u16(await self.read(USE_HOURS)) * 60
            with contextlib.suppress(Exception):
                minutes += u16(await self.read(USE_MINUTES))
            s.heater_runtime_min = minutes
        self._status1(u16(await self.read(STATUS1)))
        with contextlib.suppress(Exception):
            self._status2(u16(await self.read(STATUS2)))

        await self.notify(CURRENT_TEMP, self._notifier(self._current))
        await self.notify(BATTERY, self._notifier(self._battery))
        for uuid, apply in ((AUTO_OFF_LEFT, self._auto_off), (STATUS1, self._status1),
                            (STATUS2, self._status2)):
            with contextlib.suppress(Exception):    # missing on older firmware
                await self.notify(uuid, self._notifier(apply))
        self.changed()

    async def poll(self) -> None:
        # Status 1 does not notify on older firmware, and the read doubles as a
        # liveness check while every value is idle.
        try:
            raw = u16(await self.read(STATUS1))
        except Exception as e:
            raise VaporizerDisconnected(f"status read failed: {type(e).__name__}") from e
        self._status1(raw)
        self.changed()
