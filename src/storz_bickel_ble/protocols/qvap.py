"""Venty / Veazy ("qvap") protocol.

Single characteristic with write + notify. Every request is a 20-byte packet
whose first byte is the command; the device answers with a notification that
starts with the same command byte.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from ..exceptions import VaporizerDisconnected
from ..models import HeaterMode
from ..puff import PuffDetector
from .base import (BOOST_OFFSET, BRIGHTNESS, HEATER, HEATER_MODE, LOCATE,
                   SUPERBOOST_OFFSET, TEMPERATURE, UNIT, VIBRATION, BaseProtocol,
                   p16, tenths, u16, u24)

SERVICE = "00000000-5354-4f52-5a26-4249434b454c"
CHAR = "00000001-5354-4f52-5a26-4249434b454c"

CMD_STATUS = 0x01
CMD_VERSION = 0x02
CMD_USAGE = 0x04
CMD_IDENTITY = 0x05
CMD_SETTINGS = 0x06
CMD_LOCATE = 0x0D

# cmd 0x01 write mask (byte 1)
WRITE_TARGET = 1 << 1
WRITE_BOOST = 1 << 2
WRITE_SUPERBOOST = 1 << 3
WRITE_HEATER = 1 << 5
WRITE_SETTINGS = 1 << 7

# cmd 0x06 write mask (byte 1)
SETTINGS_WRITE_BRIGHTNESS = 1 << 0
SETTINGS_WRITE_VIBRATION = 1 << 3

BRIGHTNESS_LEVELS = 9

TEMP_NOT_AVAILABLE = 0x8000

# status byte 14
SETTINGS_FAHRENHEIT = 1 << 0
SETTINGS_SETPOINT_REACHED = 1 << 1


def parse_status(state, d: bytes) -> bool:
    """Apply a cmd 0x01 response to ``state``; False if the packet is not one."""
    if len(d) < 15 or d[0] != CMD_STATUS:
        return False
    raw_curr = u16(d, 2)
    state.current_temp = None if raw_curr == TEMP_NOT_AVAILABLE else tenths(raw_curr)
    state.target_temp = tenths(u16(d, 4))
    state.boost_offset = d[6]
    state.superboost_offset = d[7]
    state.battery = d[8]
    state.auto_shutoff_s = d[9] + d[10]     # sic: the vendor app adds the two bytes
    try:
        state.heater_mode = HeaterMode(d[11])
    except ValueError:
        state.heater_mode = HeaterMode.ON
    state.charging = bool(d[13])
    state.fahrenheit = bool(d[14] & SETTINGS_FAHRENHEIT)
    state.setpoint_reached = state.heater_on and bool(d[14] & SETTINGS_SETPOINT_REACHED)
    state.raw["status"] = bytes(d)
    return True


def parse_version(state, d: bytes) -> None:
    """cmd 0x02: [2..7] application firmware, ASCII."""
    if len(d) >= 8 and d[0] == CMD_VERSION:
        state.firmware = d[2:8].decode("ascii", "ignore").strip("\x00 ") or None
        state.raw["version"] = bytes(d)


def parse_usage(state, d: bytes) -> None:
    """cmd 0x04: [1..3] heater runtime, [4..6] charging time, minutes (u24 LE)."""
    if len(d) >= 7 and d[0] == CMD_USAGE:
        state.heater_runtime_min = u24(d, 1)
        state.charging_time_min = u24(d, 4)


def parse_identity(state, d: bytes) -> None:
    """cmd 0x05: serial = ASCII [15..16] (model prefix) + [9..14]."""
    if len(d) >= 17 and d[0] == CMD_IDENTITY:
        serial = (d[15:17] + d[9:15]).decode("ascii", "ignore").strip("\x00 ")
        if serial:
            state.serial = serial


def parse_settings(state, d: bytes) -> None:
    """cmd 0x06: [2] brightness 1-9, [5] vibration."""
    if len(d) >= 6 and d[0] == CMD_SETTINGS:
        state.brightness = round(d[2] * 100 / BRIGHTNESS_LEVELS)
        state.vibration = bool(d[5])


def packet(cmd: int, size: int = 20, **fields) -> bytearray:
    """Zero-filled request; ``fields`` maps ``b<offset>`` to a byte value."""
    p = bytearray(size)
    p[0] = cmd
    for key, value in fields.items():
        p[int(key[1:])] = value
    return p


class QvapProtocol(BaseProtocol):
    poll_interval = 0.5         # same as the vendor app
    max_misses = 4              # unanswered polls in a row before giving up
    usage_every = 120           # refresh usage counters every N polls (~1 min)
    features = frozenset({HEATER, HEATER_MODE, TEMPERATURE, BOOST_OFFSET, SUPERBOOST_OFFSET,
                          BRIGHTNESS, VIBRATION, UNIT, LOCATE})

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.puffs = PuffDetector()
        self._waiters: Dict[int, asyncio.Future] = {}
        self._misses = 0
        self._ticks = 0

    def _on_notify(self, _char, data: bytearray) -> None:
        d = bytes(data)
        if not d:
            return
        fut = self._waiters.pop(d[0], None)
        if fut and not fut.done():
            fut.set_result(d)

    async def send(self, data: bytes) -> None:
        try:
            await self.write(CHAR, data, response=True)
        except Exception:
            if not self.client.is_connected:
                raise
            await self.write(CHAR, data, response=False)

    async def request(self, cmd: int, timeout: float = 1.5, data: Optional[bytes] = None) -> bytes:
        """Send ``data`` (default: bare ``cmd`` packet) and await the matching answer."""
        fut = asyncio.get_running_loop().create_future()
        self._waiters[cmd] = fut
        try:
            await self.send(data if data is not None else packet(cmd))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._waiters.pop(cmd, None)

    async def _refresh_settings(self) -> None:
        try:
            parse_settings(self.state, await self.request(
                CMD_SETTINGS, data=packet(CMD_SETTINGS, 7)))
        except asyncio.TimeoutError:
            pass

    # --- control ----------------------------------------------------------------

    async def set_heater_mode(self, mode: HeaterMode) -> None:
        await self.send(packet(CMD_STATUS, b1=WRITE_HEATER, b11=int(mode)))

    async def set_target(self, celsius: float) -> None:
        raw = p16(round(celsius * 10))
        await self.send(packet(CMD_STATUS, b1=WRITE_TARGET, b4=raw[0], b5=raw[1]))

    async def set_boost_offset(self, celsius: int) -> None:
        await self.send(packet(CMD_STATUS, b1=WRITE_BOOST, b6=celsius))

    async def set_superboost_offset(self, celsius: int) -> None:
        await self.send(packet(CMD_STATUS, b1=WRITE_SUPERBOOST, b7=celsius))

    async def set_fahrenheit(self, on: bool) -> None:
        await self.send(packet(CMD_STATUS, b1=WRITE_SETTINGS,
                               b14=SETTINGS_FAHRENHEIT if on else 0, b15=SETTINGS_FAHRENHEIT))

    async def set_brightness(self, percent: int) -> None:
        level = max(1, min(BRIGHTNESS_LEVELS, round(percent * BRIGHTNESS_LEVELS / 100)))
        await self.send(packet(CMD_SETTINGS, 7, b1=SETTINGS_WRITE_BRIGHTNESS, b2=level))
        await self._refresh_settings()
        self.changed()

    async def set_vibration(self, on: bool) -> None:
        await self.send(packet(CMD_SETTINGS, 7, b1=SETTINGS_WRITE_VIBRATION, b5=int(on)))
        await self._refresh_settings()
        self.changed()

    async def locate(self) -> None:
        await self.send(packet(CMD_LOCATE, b1=1))

    # --- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        await self.notify(CHAR, self._on_notify)
        for _ in range(5):
            try:
                if parse_status(self.state, await self.request(CMD_STATUS, 2.0)):
                    break
            except asyncio.TimeoutError:
                pass
        else:
            raise VaporizerDisconnected("no answer to status request")
        if self.state.name and " " in self.state.name:     # "S&B VY123456" -> fallback serial
            self.state.serial = self.state.name.split(" ", 1)[1]
        for cmd, parse in ((CMD_VERSION, parse_version), (CMD_IDENTITY, parse_identity),
                           (CMD_USAGE, parse_usage)):
            try:
                parse(self.state, await self.request(cmd))
            except asyncio.TimeoutError:
                pass
        await self._refresh_settings()
        self.puffs.reset()
        self.puffs.feed(self.state)
        self.changed()

    async def poll(self) -> None:
        try:
            d = await self.request(CMD_STATUS)
        except Exception as e:
            if not self.client.is_connected:
                raise VaporizerDisconnected("device disconnected") from e
            self._misses += 1
            if self._misses >= self.max_misses:
                raise VaporizerDisconnected(f"{self._misses} status requests unanswered") from e
            return
        self._misses = 0
        if parse_status(self.state, d):
            self.puffs.feed(self.state)
            self.changed()
        self._ticks += 1
        if self._ticks % self.usage_every == 0:
            try:
                parse_usage(self.state, await self.request(CMD_USAGE))
                self.changed()
            except asyncio.TimeoutError:
                pass
