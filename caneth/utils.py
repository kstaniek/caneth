def parse_hex_bytes(s: str) -> bytes:
    """
    Parse a human-friendly hex string into bytes.
    Accepts formats like:
      "12 34 56", "0x12,0x34,0x56", "12-34-56", "123456", "12:34:56"
    """
    s = s.strip()
    if not s:
        return b""
    cleaned = s.replace("0x", "").replace("0X", "").replace(",", " ").replace("-", " ").replace(":", " ").strip()
    if " " in cleaned:
        parts = [p for p in cleaned.split() if p]
        return bytes(int(p, 16) & 0xFF for p in parts)
    if len(cleaned) % 2 != 0:
        raise ValueError("Odd-length hex string; add a leading 0 or use separators")
    return bytes(int(cleaned[i : i + 2], 16) & 0xFF for i in range(0, len(cleaned), 2))
