"""Connection-level client, a one-call ``connect()`` and an auto-reconnecting monitor."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import AsyncIterator, Callable, FrozenSet, List, Optional, Union

from bleak import BleakClient
from bleak.backends.device import BLEDevice

from .discovery import detect_family, find_device
from .exceptions import (UnsupportedDevice, UnsupportedOperation, VaporizerDisconnected,
                         VaporizerError)
from .models import DeviceFamily, HeaterMode, VaporizerState
from .protocols import base as feat
from .protocols import handler_for

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[VaporizerState], None]


class VaporizerClient:
    """Connection to one Storz & Bickel device: state, change stream and control.

    >>> async with VaporizerClient(device) as vap:
    ...     await vap.set_temperature(185)
    ...     await vap.heater_on()
    ...     async for state in vap.updates():
    ...         print(state.target_temp, state.battery, state.puffs)

    Control methods raise :class:`UnsupportedOperation` when the device family
    lacks the feature (see :meth:`supports`) and ``ValueError`` for values out
    of the device's range.

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
            self._state.max_temp = self._handler.temp_range[1]
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

    @property
    def features(self) -> FrozenSet[str]:
        """Names of the control features this device supports."""
        return handler_for(self._family).features

    def supports(self, feature: str) -> bool:
        """``feature``: heater, heater_mode, temperature, boost_offset,
        superboost_offset, pump, brightness, vibration, auto_off, unit, locate."""
        return feature in self.features

    # --- control ---------------------------------------------------------------

    def _require(self, feature: str):
        """Protocol class for range checks; raises if the feature is unsupported."""
        if not self.supports(feature):
            raise UnsupportedOperation(
                f"{self._family.display_name} does not support {feature.replace('_', ' ')}")
        return handler_for(self._family)

    def _live(self):
        if not self.is_connected:
            raise VaporizerDisconnected("not connected")
        return self._handler

    @staticmethod
    def _check(name: str, value: float, lo: float, hi: float) -> None:
        if not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {value:g}")

    async def heater_on(self) -> None:
        await self.set_heater_mode(HeaterMode.ON)

    async def heater_off(self) -> None:
        await self.set_heater_mode(HeaterMode.OFF)

    async def set_heater(self, on: bool) -> None:
        await self.set_heater_mode(HeaterMode.ON if on else HeaterMode.OFF)

    async def boost(self) -> None:
        """Venty / Veazy only."""
        await self.set_heater_mode(HeaterMode.BOOST)

    async def superboost(self) -> None:
        """Venty / Veazy only."""
        await self.set_heater_mode(HeaterMode.SUPERBOOST)

    async def set_heater_mode(self, mode: Union[HeaterMode, int]) -> None:
        mode = HeaterMode(mode)
        need = feat.HEATER if mode in (HeaterMode.OFF, HeaterMode.ON) else feat.HEATER_MODE
        self._require(need)
        await self._live().set_heater_mode(mode)

    async def set_temperature(self, celsius: float) -> None:
        """Base target temperature in °C (boost offsets are added on top)."""
        spec = self._require(feat.TEMPERATURE)
        self._check("temperature", celsius, *spec.temp_range)
        await self._live().set_target(celsius)

    async def set_boost_offset(self, celsius: int) -> None:
        spec = self._require(feat.BOOST_OFFSET)
        self._check("boost offset", celsius, *spec.offset_range)
        await self._live().set_boost_offset(int(celsius))

    async def set_superboost_offset(self, celsius: int) -> None:
        """Extra °C on top of the boost offset in superboost."""
        spec = self._require(feat.SUPERBOOST_OFFSET)
        self._check("superboost offset", celsius, *spec.offset_range)
        await self._live().set_superboost_offset(int(celsius))

    async def pump_on(self) -> None:
        await self.set_pump(True)

    async def pump_off(self) -> None:
        await self.set_pump(False)

    async def set_pump(self, on: bool) -> None:
        """Volcano only."""
        self._require(feat.PUMP)
        await self._live().set_pump(bool(on))

    async def set_brightness(self, percent: int) -> None:
        """0-100 %. Venty / Veazy round to their 9 levels (minimum level 1)."""
        self._require(feat.BRIGHTNESS)
        self._check("brightness", percent, 0, 100)
        await self._live().set_brightness(int(percent))

    async def set_vibration(self, on: bool) -> None:
        self._require(feat.VIBRATION)
        await self._live().set_vibration(bool(on))

    async def set_auto_off(self, seconds: int) -> None:
        """Auto shut-off time (Crafty: 10-300 s, Volcano: 60-21600 s)."""
        spec = self._require(feat.AUTO_OFF)
        self._check("auto-off", seconds, *spec.auto_off_range)
        await self._live().set_auto_off(int(seconds))

    async def set_unit(self, unit: str) -> None:
        """Unit shown on the device display: ``"C"`` or ``"F"``."""
        u = unit.strip().upper().lstrip("°")
        if u not in ("C", "F"):
            raise ValueError(f"unit must be 'C' or 'F', got {unit!r}")
        self._require(feat.UNIT)
        await self._live().set_fahrenheit(u == "F")

    async def locate(self) -> None:
        """Make the device signal itself (vibrate / blink)."""
        self._require(feat.LOCATE)
        await self._live().locate()

    async def wait_for(self, predicate: Callable[[VaporizerState], bool],
                       timeout: float = 10.0) -> VaporizerState:
        """Wait until ``predicate(state)`` is true, e.g. ``lambda s: s.setpoint_reached``.

        Raises ``asyncio.TimeoutError`` after ``timeout`` seconds.
        """
        async def wait():
            async for state in self.updates():
                if predicate(state):
                    return state

        return await asyncio.wait_for(wait(), timeout)

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


@contextlib.asynccontextmanager
async def connect(name_filter: Optional[str] = None, *, address: Optional[str] = None,
                  timeout: float = 10.0, **client_kwargs) -> AsyncIterator[VaporizerClient]:
    """Find a device and connect in one step.

    >>> async with connect() as vap:            # or connect("VY123456")
    ...     await vap.heater_on()
    """
    device = await find_device(name_filter, address=address, timeout=timeout)
    if device is None:
        wanted = name_filter or address
        raise VaporizerError("no Storz & Bickel device found"
                             + (f" matching {wanted!r}" if wanted else ""))
    async with VaporizerClient(device, **client_kwargs) as vap:
        yield vap


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
