"""Unofficial async Bluetooth LE library for Storz & Bickel vaporizers."""

from .client import VaporizerClient, monitor
from .discovery import detect_family, discover, find_device
from .exceptions import UnsupportedDevice, VaporizerDisconnected, VaporizerError
from .models import DeviceFamily, HeaterMode, VaporizerState
from .puff import PuffDetector

__version__ = "0.1.0"

__all__ = [
    "DeviceFamily",
    "HeaterMode",
    "PuffDetector",
    "UnsupportedDevice",
    "VaporizerClient",
    "VaporizerDisconnected",
    "VaporizerError",
    "VaporizerState",
    "detect_family",
    "discover",
    "find_device",
    "monitor",
]
