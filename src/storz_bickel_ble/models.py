"""Device-independent data model."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field, replace
from typing import Optional


class DeviceFamily(str, enum.Enum):
    VENTY = "venty"
    VEAZY = "veazy"
    CRAFTY = "crafty"          # Crafty+ and Mighty+ share one protocol
    VOLCANO = "volcano"        # Volcano Hybrid

    @property
    def display_name(self) -> str:
        return {
            DeviceFamily.VENTY: "Venty",
            DeviceFamily.VEAZY: "Veazy",
            DeviceFamily.CRAFTY: "Crafty+/Mighty+",
            DeviceFamily.VOLCANO: "Volcano Hybrid",
        }[self]


class HeaterMode(enum.IntEnum):
    OFF = 0
    ON = 1
    BOOST = 2
    SUPERBOOST = 3


@dataclass
class VaporizerState:
    """Snapshot of everything the library knows about a device.

    Temperatures are always degrees Celsius (the protocol never uses Fahrenheit;
    ``fahrenheit`` only tells which unit the device itself displays).
    Fields a given device or firmware does not report stay ``None``.
    """

    family: Optional[DeviceFamily] = None
    name: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None

    current_temp: Optional[float] = None
    target_temp: Optional[float] = None
    boost_offset: Optional[int] = None
    superboost_offset: Optional[int] = None     # added on top of the boost offset
    max_temp: Optional[float] = None            # device limit; boosts are capped here

    heater_mode: HeaterMode = HeaterMode.OFF
    setpoint_reached: bool = False
    auto_shutoff_s: Optional[int] = None
    pump_on: Optional[bool] = None

    battery: Optional[int] = None
    charging: Optional[bool] = None
    fahrenheit: Optional[bool] = None

    brightness: Optional[int] = None       # display / LED brightness, percent
    vibration: Optional[bool] = None
    auto_off_setting_s: Optional[int] = None   # configured auto-off time (Crafty, Volcano)

    heater_runtime_min: Optional[int] = None
    charging_time_min: Optional[int] = None

    puffs: int = 0
    puffing: bool = False

    updated: float = field(default=0.0, compare=False)
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def heater_on(self) -> bool:
        return self.heater_mode != HeaterMode.OFF

    @property
    def boost(self) -> bool:
        return self.heater_mode == HeaterMode.BOOST

    @property
    def superboost(self) -> bool:
        return self.heater_mode == HeaterMode.SUPERBOOST

    @property
    def effective_target_temp(self) -> Optional[float]:
        """Target including the active boost (+ superboost) offset, capped at ``max_temp``."""
        if self.target_temp is None:
            return None
        t = self.target_temp
        if (self.boost or self.superboost) and self.boost_offset is not None:
            t += self.boost_offset
        if self.superboost and self.superboost_offset is not None:
            t += self.superboost_offset
        return min(t, self.max_temp) if self.max_temp is not None else t

    @property
    def age(self) -> float:
        """Seconds since the last update from the device."""
        return time.time() - self.updated if self.updated else float("inf")

    def copy(self) -> "VaporizerState":
        return replace(self, raw=dict(self.raw))
