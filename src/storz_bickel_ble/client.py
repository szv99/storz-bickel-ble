"""Connection-level client and an auto-reconnecting monitor."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import AsyncIterator, Callable, List, Optional, Union

from bleak import BleakClient
from bleak.backends.device import BLEDevice

from .discovery import detect_family, find_device
from .exceptions import UnsupportedDevice, VaporizerDisconnected
from .models import DeviceFamily, VaporizerState
from .protocols import handler_for

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[VaporizerState], None]


class VaporizerClient:
    """Read-only connection to one Storz & Bickel device.

    >>> async with VaporizerClient(device) as vap:
    ...     async for state in vap.updates():
    ...         print(state.target_temp, state.battery, state.puffs)

    ``device`` is a :class:`bleak.backends.device.BLEDevice` (preferred, from
    :func:`find_device`) or an address string; with an address, pass
    ``family`` explicitly.
    """

    def __init__(self, device: Union[BLEDevice, str], *,
                 family: Optional[DeviceFamily] = None,
                 connect_timeout: float = 15.0, op_timeout: float = 8.0):
        self._device = device
        name = device.name if isinstance(device, BLEDevice) else None
        self._family = family or detect_family(name)
        if self._family is None:
            raise UnsupportedDevice(f"unknown device {name or device!r}; pass family=")
        self._connect_timeout = connect_timeout
        self._op_timeout = op_timeout
        self._client: Optional[BleakClient] = None
        self._task: Optional[asyncio.Task] = None
        self._state = VaporizerState(family=self._family, name=name)
        self._listeners: List[Listener] = []
        self._queues: List[asyncio.Queue] = []
        self._last_sent: Optional[VaporizerState] = None
        self._lost: Optional[BaseException] = None

    # --- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        self._lost = None
        self._client = BleakClient(self._device, timeout=self._connect_timeout,
                                   disconnected_callback=lambda _c: self._on_lost(
                                       VaporizerDisconnected("device disconnected")))
        await asyncio.wait_for(self._client.connect(), self._connect_timeout + 5)
        try:
            self._handler = handler_for(self._family)(
                self._client, self._state, self._changed, self._op_timeout)
            await self._handler.start()
        except BaseException:
            await self.disconnect()
            raise
        self._task = asyncio.create_task(self._run())

    async def disconnect(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
            self._task = None
        if self._client:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._client.disconnect(), 5)
        self._on_lost(VaporizerDisconnected("disconnected by client"))

    async def __aenter__(self) -> "VaporizerClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._lost is None and self._client is not None and self._client.is_connected

    @property
    def family(self) -> DeviceFamily:
        return self._family

    @property
    def state(self) -> VaporizerState:
        """Copy of the latest state."""
        return self._state.copy()

    # --- subscriptions -------------------------------------------------------

    def add_listener(self, callback: Listener) -> Callable[[], None]:
        """Call ``callback(state)`` on every change; returns an unsubscribe function."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    async def updates(self) -> AsyncIterator[VaporizerState]:
        """Yield the current state, then every change.

        Raises :class:`VaporizerDisconnected` when the link is lost.
        """
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        try:
            if self._lost:
                raise self._lost
            if self._state.updated:
                yield self.state
            while True:
                item = await q.get()
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self._queues.remove(q)

    # --- internals -----------------------------------------------------------

    def _changed(self) -> None:
        self._state.updated = time.time()
        if self._last_sent is not None and self._state == self._last_sent:
            return
        snap = self._state.copy()
        self._last_sent = snap
        for cb in list(self._listeners):
            try:
                cb(snap.copy())
            except Exception:
                _LOGGER.exception("listener failed")
        for q in self._queues:
            q.put_nowait(snap.copy())

    def _on_lost(self, exc: BaseException) -> None:
        if self._lost:
            return
        self._lost = exc
        for q in self._queues:
            q.put_nowait(exc)

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._handler.poll_interval)
                await self._handler.poll()
        except asyncio.CancelledError:
            raise
        except VaporizerDisconnected as e:
            self._on_lost(e)
        except Exception as e:
            self._on_lost(VaporizerDisconnected(f"{type(e).__name__}: {e}"))
        with contextlib.suppress(Exception):
            if self._client:
                await asyncio.wait_for(self._client.disconnect(), 5)


async def monitor(name_filter: Optional[str] = None, *, address: Optional[str] = None,
                  scan_timeout: float = 10.0, retry_delay: float = 2.0,
                  **client_kwargs) -> AsyncIterator[Optional[VaporizerState]]:
    """Follow a device forever, reconnecting as needed.

    Yields every state change, and ``None`` once each time the device is lost
    or cannot be found (so callers can clear their UI).

    >>> async for state in monitor("VENTY"):
    ...     print("offline" if state is None else state)
    """
    online = True
    while True:
        try:
            dev = await find_device(name_filter, address=address, timeout=scan_timeout)
        except Exception as e:      # adapter off, permission denied, ...
            _LOGGER.warning("scan failed: %s", e)
            dev = None
        if dev is None:
            if online:
                online = False
                yield None
            await asyncio.sleep(retry_delay)
            continue
        try:
            async with VaporizerClient(dev, **client_kwargs) as vap:
                online = True
                async for state in vap.updates():
                    yield state
        except Exception as e:
            _LOGGER.info("connection to %s ended: %s", dev.name, e)
        if online:
            online = False
            yield None
        await asyncio.sleep(retry_delay)
