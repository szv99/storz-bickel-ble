"""Library exceptions."""


class VaporizerError(Exception):
    """Base class for all errors raised by this library."""


class VaporizerDisconnected(VaporizerError):
    """The BLE link was lost or the device stopped answering."""


class UnsupportedDevice(VaporizerError):
    """The device name / services do not match a known Storz & Bickel family."""


class UnsupportedOperation(VaporizerError):
    """The connected device family does not support this command."""
