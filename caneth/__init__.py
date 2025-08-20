"""Top-level package for caneth.

This module exposes the primary public API and configures library-friendly logging.
"""

from .client import CANFrame, WaveShareCANClient
from .utils import parse_hex_bytes

__all__ = ["CANFrame", "WaveShareCANClient", "parse_hex_bytes"]

# Library-friendly logging: don't emit logs unless the app configures handlers.
import logging as _logging

_logging.getLogger(__name__).addHandler(_logging.NullHandler())
