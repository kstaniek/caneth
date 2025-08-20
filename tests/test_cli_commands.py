import asyncio
from collections.abc import Callable

import pytest
from caneth import cli
from caneth.client import CANFrame


class FakeClient:
    """Test double for WaveShareCANClient used by CLI tests."""

    # Controls for the next wait() call in this process
    next_wait_frame: CANFrame | None = None
    raise_timeout: bool = False

    def __init__(self, host: str, port: int, name: str = "") -> None:
        self.host = host
        self.port = port
        self.name = name
        self.started = False
        self.connected_waits = 0
        self.closed = False
        self.on_frame_handlers: list[Callable[[CANFrame], None]] = []
        self.sent: list[tuple[int, bytes, bool | None, bool]] = []

    # --- lifecycle ---
    async def start(self) -> None:
        self.started = True

    async def wait_connected(self, timeout: float | None = None) -> None:
        self.connected_waits += 1

    async def close(self) -> None:
        self.closed = True

    # --- api ---
    def on_frame(self, cb) -> None:
        self.on_frame_handlers.append(cb)

    async def send(self, can_id: int, data: bytes, *, extended=None, rtr: bool = False) -> None:
        self.sent.append((can_id, bytes(data), extended, rtr))

    async def wait_for(self, can_id: int, *, d0=None, d1=None, timeout=None) -> CANFrame:
        if FakeClient.raise_timeout:
            # Match CLI's except asyncio.TimeoutError:
            raise asyncio.TimeoutError("timeout (fake)")
        if FakeClient.next_wait_frame is None:
            # sensible default: return an empty frame with the id
            return CANFrame(can_id=can_id, data=b"", extended=can_id > 0x7FF, rtr=False, dlc=0)
        return FakeClient.next_wait_frame


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """Patch the CLI to use the FakeClient instead of the real client."""
    monkeypatch.setattr(cli, "WaveShareCANClient", FakeClient)
    # reset controls for each test
    FakeClient.next_wait_frame = None
    FakeClient.raise_timeout = False


# --------------------- tests: send ---------------------


def test_cli_send_happy_path(capsys):
    # --extended + --rtr, id and data in hex
    code = cli.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "20001",
            "send",
            "--id",
            "0x123",
            "--data",
            "01 02 03",
            "--extended",
            "--rtr",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Sent frame id=0x123" in out
    assert "rtr=True" in out
    # Confirm the fake recorded the send
    # (We can't reach into the instance easily here, but the print is a good proxy.)


def test_cli_send_bad_data_is_reported(capsys):
    code = cli.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "20001",
            "send",
            "--id",
            "0x123",
            "--data",
            "GG",  # invalid hex
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Error parsing data bytes:" in out


# --------------------- tests: wait ---------------------


def test_cli_wait_match_prints_frame(capsys):
    # Prepare a frame the fake will return
    FakeClient.next_wait_frame = CANFrame(can_id=0x123, data=b"\x01\x02", extended=False, rtr=False, dlc=2)

    code = cli.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "20001",
            "wait",
            "--id",
            "0x123",
            "--d0",
            "0x01",
            "--d1",
            "0x02",
            "--wait-timeout",
            "0.5",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Matched:" in out
    assert "id=0x123" in out
    U = out.upper()
    assert ("DATA=0102" in U) or ("DATA=[01 02]" in U)


def test_cli_wait_timeout_message(capsys):
    FakeClient.raise_timeout = True
    code = cli.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "20001",
            "wait",
            "--id",
            "0x123",
            "--wait-timeout",
            "0.01",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Timeout waiting for frame" in out


# --------------------- tests: watch ---------------------


def test_cli_watch_exits_on_keyboardinterrupt(monkeypatch, capsys):
    # Make asyncio.sleep in cli raise KeyboardInterrupt so the watch loop exits promptly
    async def sleep_ki(_secs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.asyncio, "sleep", sleep_ki)

    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "watch"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Connected to 127.0.0.1:20001. Watching for frames" in out


# --------------------- tests: parsers ---------------------


def test_parse_can_id_accepts_plain_hex():
    # plain hex without 0x prefix should be accepted
    assert cli._parse_can_id("1e5d") == int("1e5d", 16)
    assert cli._parse_can_id("0x1E5D") == int("1e5d", 16)
    assert cli._parse_can_id("777") == 777


def test_parse_byte_variants():
    assert cli._parse_byte("0xFF") == 0xFF
    assert cli._parse_byte("ff") == 0xFF
    assert cli._parse_byte("10") == 10
    with pytest.raises(ValueError):
        cli._parse_byte("300")  # out of range
    with pytest.raises(ValueError):
        cli._parse_byte("zz")  # bad value
