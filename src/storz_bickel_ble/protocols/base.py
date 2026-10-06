"""Shared plumbing for the per-family protocol handlers."""

from __future__ import annotations

import asyncio
from typing import Callable

from bleak import BleakClient

from ..models import VaporizerState


class BaseProtocol:
    """One instance per connection.

    ``start`` performs the initial reads and subscriptions; ``poll`` is awaited
    in a loop by the client and must raise
    :class:`~storz_bickel_ble.exceptions.VaporizerDisconnected` when the link is
    gone. Handlers write into ``self.state`` and call ``self.changed()``.
    """

    poll_interval = 5.0

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


def tenths(raw: int) -> float:
    """Protocol temperatures are 1/10 °C; return an int-valued float when whole."""
    v = raw / 10
    return float(int(v)) if v == int(v) else v
