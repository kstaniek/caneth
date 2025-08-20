# caneth

caneth
======
Asyncio CAN client for Waveshare 2-CH-CAN-TO-ETH devices.

This package provides:
  - WaveShareCANClient: an asyncio TCP client speaking the device's
    13-byte "transparent" CAN framing (1 byte flags/DLC + 4-byte CAN ID + 8 data bytes).
  - A callback registry for exact (CAN ID, first two data bytes) matches.
  - Global observers for all frames.
  - A one-shot `wait_for` method to await a specific frame.
  - A CLI with `watch`, `send`, `wait`, and `repl`.