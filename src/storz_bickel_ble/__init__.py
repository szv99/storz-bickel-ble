"""Unofficial async Bluetooth LE library for Storz & Bickel vaporizers."""

from .client import VaporizerClient, connect, monitor
from .discovery import detect_family, discover, find_device
from .exceptions import (UnsupportedDevice, UnsupportedOperation, VaporizerDisconnected,
                         VaporizerError)
from .models import DeviceFamily, HeaterMode, VaporizerState
from .puff import PuffDetector

__version__ = "0.2.0"

__all__ = [
    "DeviceFamily",
    "HeaterMode",
    "PuffDetector",
    "UnsupportedDevice",
    "UnsupportedOperation",
    "VaporizerClient",
    "VaporizerDisconnected",
    "VaporizerError",
    "VaporizerState",
    "connect",
    "detect_family",
    "discover",
    "find_device",
    "monitor",
]
