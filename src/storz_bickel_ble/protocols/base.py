"""Shared plumbing for the per-family protocol handlers."""

from __future__ import annotations

import asyncio
from typing import Callable, FrozenSet, Tuple

from bleak import BleakClient

from ..models import HeaterMode, VaporizerState

# Feature names used by VaporizerClient.supports() and the control methods.
HEATER = "heater"
HEATER_MODE = "heater_mode"           # boost / superboost via BLE
TEMPERATURE = "temperature"
BOOST_OFFSET = "boost_offset"
SUPERBOOST_OFFSET = "superboost_offset"
PUMP = "pump"
BRIGHTNESS = "brightness"
VIBRATION = "vibration"
AUTO_OFF = "auto_off"
UNIT = "unit"
LOCATE = "locate"


class BaseProtocol:
    """One instance per connection.

    ``start`` performs the initial reads and subscriptions; ``poll`` is awaited
    in a loop by the client and must raise
    :class:`~storz_bickel_ble.exceptions.VaporizerDisconnected` when the link is
    gone. Handlers write into ``self.state`` and call ``self.changed()``.

    Control methods are only called by the client after checking ``features``
    and the ranges below, so they can assume valid input.
    """

    poll_interval = 5.0
    features: FrozenSet[str] = frozenset()
    temp_range: Tuple[float, float] = (40.0, 210.0)
    offset_range: Tuple[int, int] = (0, 99)
    auto_off_range: Tuple[int, int] = (0, 0)

    def __init__(self, client: BleakClient, state: VaporizerState,
                 changed: Callable[[], None], op_timeout: float = 8.0):
        self.client = client
        self.state = state
        self.changed = changed
        self.op_timeout = op_timeout

    async def start(self) -> None:
        raise NotImplementedError

    async def poll(self) -> None:
        raise NotImplementedError

    # --- control (overridden per family) ----------------------------------------

    async def set_heater_mode(self, mode: HeaterMode) -> None:
        raise NotImplementedError

    async def set_target(self, celsius: float) -> None:
        raise NotImplementedError

    async def set_boost_offset(self, celsius: int) -> None:
        raise NotImplementedError

    async def set_superboost_offset(self, celsius: int) -> None:
        raise NotImplementedError

    async def set_pump(self, on: bool) -> None:
        raise NotImplementedError

    async def set_brightness(self, percent: int) -> None:
        raise NotImplementedError

    async def set_vibration(self, on: bool) -> None:
        raise NotImplementedError

    async def set_auto_off(self, seconds: int) -> None:
        raise NotImplementedError

    async def set_fahrenheit(self, on: bool) -> None:
        raise NotImplementedError

    async def locate(self) -> None:
        raise NotImplementedError

    # --- GATT helpers -------------------------------------------------------------
    # CoreBluetooth / BlueZ calls can hang forever, so every GATT op is bounded.

    async def read(self, uuid: str) -> bytes:
        return bytes(await asyncio.wait_for(self.client.read_gatt_char(uuid), self.op_timeout))

    async def write(self, uuid: str, data: bytes, response: bool = True) -> None:
        await asyncio.wait_for(self.client.write_gatt_char(uuid, data, response=response),
                               self.op_timeout)

    async def notify(self, uuid: str, callback) -> None:
        await asyncio.wait_for(self.client.start_notify(uuid, callback), self.op_timeout)


def u16(data: bytes, offset: int = 0) -> int:
    return data[offset] | data[offset + 1] << 8


def u24(data: bytes, offset: int = 0) -> int:
    return data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16


def p16(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


def p32(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def tenths(raw: int) -> float:
    """Protocol temperatures are 1/10 °C; return an int-valued float when whole."""
    v = raw / 10
    return float(int(v)) if v == int(v) else v
