"""python-can interface for CSS Electronics devices with a USB serial interface (CANmod, CLX000).

Registered with python-can as ``interface="csscan_serial"``. Full documentation: ``README.md`` in this package
directory (``importlib.resources.files("python_can_csscan_serial") / "README.md"``).
"""

from ._bus import CSSCANSerialBus

__all__ = ["CSSCANSerialBus"]
