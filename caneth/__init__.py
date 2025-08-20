"""caneth — Asyncio CAN client for Waveshare 2-CH-CAN-TO-ETH.

Public API:
    - WaveShareCANClient: Async client with auto-reconnect and callback registry
    - CANFrame: Dataclass representing a CAN frame
    - parse_hex_bytes: Utility to parse human-friendly hex strings
"""

from .client import CANFrame, WaveShareCANClient
from .utils import parse_hex_bytes

__all__ = ["WaveShareCANClient", "CANFrame", "parse_hex_bytes"]
