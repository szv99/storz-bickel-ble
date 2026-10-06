"""Scanning and device-family detection."""

from __future__ import annotations

from typing import List, Optional, Tuple

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from .models import DeviceFamily

NAME_PREFIXES = ("S&B", "STORZ&BICKEL")


def detect_family(name: Optional[str]) -> Optional[DeviceFamily]:
    """Map an advertised name to a device family (same rules as the vendor app).

    Every Storz & Bickel name that is not a Volcano, Venty or Veazy is handled
    with the Crafty protocol (Crafty+, Mighty+).
    """
    n = (name or "").upper()
    if not n.replace(" ", "").startswith(NAME_PREFIXES):
        return None
    if "S&B VOLCANO" in n:
        return DeviceFamily.VOLCANO
    if "S&B VY" in n:
        return DeviceFamily.VENTY
    if "S&B VZ" in n:
        return DeviceFamily.VEAZY
    return DeviceFamily.CRAFTY


def _name(device: BLEDevice, adv=None) -> Optional[str]:
    return (adv.local_name if adv is not None and adv.local_name else None) or device.name


async def discover(timeout: float = 10.0) -> List[Tuple[BLEDevice, DeviceFamily]]:
    """Scan for ``timeout`` seconds and return every Storz & Bickel device seen."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for device, adv in found.values():
        family = detect_family(_name(device, adv))
        if family:
            out.append((device, family))
    return out


async def find_device(name_filter: Optional[str] = None, *, address: Optional[str] = None,
                      timeout: float = 10.0) -> Optional[BLEDevice]:
    """Return the first matching device as soon as it is seen, or None.

    ``name_filter`` is a case-insensitive substring of the advertised name;
    ``address`` is the MAC address (or CoreBluetooth UUID on macOS).
    """

    def match(device: BLEDevice, adv) -> bool:
        if address and device.address.lower() != address.lower():
            return False
        name = _name(device, adv)
        if not detect_family(name):
            return False
        return not name_filter or name_filter.lower() in (name or "").lower()

    return await BleakScanner.find_device_by_filter(match, timeout=timeout)
