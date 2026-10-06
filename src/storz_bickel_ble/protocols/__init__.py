"""Per-family protocol handlers."""

from typing import Type

from ..models import DeviceFamily
from .base import BaseProtocol
from .crafty import CraftyProtocol
from .qvap import QvapProtocol
from .volcano import VolcanoProtocol


def handler_for(family: DeviceFamily) -> Type[BaseProtocol]:
    return {
        DeviceFamily.VENTY: QvapProtocol,
        DeviceFamily.VEAZY: QvapProtocol,
        DeviceFamily.CRAFTY: CraftyProtocol,
        DeviceFamily.VOLCANO: VolcanoProtocol,
    }[family]
