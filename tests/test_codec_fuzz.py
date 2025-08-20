import pytest
from caneth.client import CANFrame


def _encode(can_id, data=b"", extended=None, rtr=False):
    if extended is None:
        extended = can_id > 0x7FF
    dlc = len(data)
    b0 = (0x80 if extended else 0) | (0x40 if rtr else 0) | (dlc & 0x0F)
    buf = bytearray(13)
    buf[0] = b0
    buf[1:5] = int(can_id).to_bytes(4, "big", signed=False)
    buf[5 : 5 + dlc] = data
    return bytes(buf)


@pytest.mark.parametrize("dlc", range(0, 9))
def test_roundtrip_valid_dlc(dlc):
    raw = _encode(0x123, bytes(range(dlc)))
    f = CANFrame.from_bytes(raw)
    assert f.dlc == dlc
    assert f.data == bytes(range(dlc))
    back = f.to_bytes()
    # Check header and ID unchanged; payload up to dlc matches
    assert back[0] == raw[0]
    assert back[1:5] == raw[1:5]
    assert back[5 : 5 + dlc] == raw[5 : 5 + dlc]


def test_invalid_dlc_raises():
    raw = bytearray(13)
    raw[0] = 0x80 | 0x09  # extended + dlc=9 (invalid)
    with pytest.raises(ValueError):
        CANFrame.from_bytes(bytes(raw))
