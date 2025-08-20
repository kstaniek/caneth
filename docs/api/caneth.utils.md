# caneth.utils

Utility helpers for caneth.

## Functions

### parse_hex_bytes `(s: str) -> bytes`

Parse a human-friendly hex string into bytes.

    Accepts formats like (case-insensitive):
        "12 34 56"
        "0x12,0x34,0x56"
        "12-34-56"
        "12:34:56"
        "123456"

    Returns:
        bytes: parsed byte sequence.

    Raises:
        ValueError: on odd-length hex without separators.