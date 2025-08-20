# tests/helpers.py
def build_frame(can_id: int, data: bytes, extended: bool | None = None, rtr: bool = False) -> bytes:
    if extended is None:
        extended = can_id > 0x7FF
    dlc = len(data)
    if not (0 <= dlc <= 8):
        raise ValueError("data length must be 0..8")
    b0 = (0x80 if extended else 0) | (0x40 if rtr else 0) | (dlc & 0x0F)
    buf = bytearray(13)
    buf[0] = b0
    buf[1:5] = int(can_id).to_bytes(4, "big", signed=False)
    buf[5 : 5 + dlc] = data
    return bytes(buf)
