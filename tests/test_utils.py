import pytest
from caneth.utils import parse_hex_bytes


def test_parse_hex_bytes_variants():
    assert parse_hex_bytes("12 34 56") == b"\x12\x34\x56"
    assert parse_hex_bytes("0x12,0xFF,0x00") == b"\x12\xff\x00"
    assert parse_hex_bytes("12-34-56") == b"\x12\x34\x56"
    assert parse_hex_bytes("12:34:56") == b"\x12\x34\x56"
    assert parse_hex_bytes("123456") == b"\x12\x34\x56"
    with pytest.raises(ValueError):
        parse_hex_bytes("12345")  # odd length
