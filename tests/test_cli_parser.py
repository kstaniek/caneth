import pytest
from caneth.cli import _parse_byte, _parse_can_id


def test_parse_can_id_accepts_hex_without_prefix():
    assert _parse_can_id("1e5d") == 0x1E5D
    assert _parse_can_id("0x1e5d") == 0x1E5D
    assert _parse_can_id("789") == 789


def test_parse_byte_accepts_variants():
    assert _parse_byte("ff") == 0xFF
    assert _parse_byte("0x10") == 0x10
    assert _parse_byte("17") == 17
    with pytest.raises(ValueError):
        _parse_byte("2FF")
    with pytest.raises(ValueError):
        _parse_byte("300")
