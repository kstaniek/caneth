import asyncio
import builtins
from collections.abc import Callable, Iterator

import pytest
from caneth import cli
from caneth.client import CANFrame


class FakeClient:
    """Test double for WaveShareCANClient used by CLI REPL tests."""

    instances: list["FakeClient"] = []
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
        self.registered: list[tuple[int, int | None, int | None, Callable[[CANFrame], None]]] = []
        FakeClient.instances.append(self)

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

    def register_callback(
        self,
        can_id: int,
        d0: int | None = None,
        d1: int | None = None,
        callback: Callable[[CANFrame], None] | None = None,
    ) -> None:
        if callback is None:
            raise ValueError("callback required")
        self.registered.append((can_id, d0, d1, callback))

    async def send(self, can_id: int, data: bytes, *, extended=None, rtr: bool = False) -> None:
        self.sent.append((can_id, bytes(data), extended, rtr))

    async def wait_for(self, can_id: int, *, d0=None, d1=None, timeout=None) -> CANFrame:
        if FakeClient.raise_timeout:
            raise asyncio.TimeoutError("timeout (fake)")
        if FakeClient.next_wait_frame is None:
            return CANFrame(can_id=can_id, data=b"", extended=can_id > 0x7FF, rtr=False, dlc=0)
        return FakeClient.next_wait_frame


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """Patch the CLI to use the FakeClient instead of the real client."""
    monkeypatch.setattr(cli, "WaveShareCANClient", FakeClient)
    # reset class-level controls for each test
    FakeClient.instances.clear()
    FakeClient.next_wait_frame = None
    FakeClient.raise_timeout = False


def _scripted_input(monkeypatch, lines: list[str]):
    """Patch builtins.input to return scripted lines, then 'quit' if exhausted."""
    it: Iterator[str] = iter(lines)

    def _fake_input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            return "quit"

    monkeypatch.setattr(builtins, "input", _fake_input)


@pytest.mark.parametrize("id_text", ["0x123", "123", "1e5d"])  # hex with/without 0x, decimal
def test_repl_on_variants_and_send_wait(monkeypatch, capsys, id_text):
    # Prepare a matched frame for the 'wait' command
    FakeClient.next_wait_frame = CANFrame(0x123, b"\x01\x02", extended=False, rtr=False, dlc=2)

    # Commands: on (id only), on (id d0), on (id d0 d1), send, wait, quit
    _scripted_input(
        monkeypatch,
        [
            f"on {id_text}",
            "on 0x123 01",
            "on 0x123 01 02",
            "send 0x123 0102 std",
            "wait 0x123 01 02 0.2",
            "quit",
        ],
    )
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out

    # Expected ID string according to CLI parsing
    expected_id = f"0x{cli._parse_can_id(id_text):X}"

    # Match current REPL output style:
    #   watch #1: id=0x...
    #   watch #2: id=0x... d0=01
    #   watch #3: id=0x... d0=01 d1=02
    assert f"watch #1: id={expected_id}" in out
    assert "watch #2: id=0x123 d0=01" in out
    assert "watch #3: id=0x123 d0=01 d1=02" in out

    # Sent confirmation
    assert "Sent." in out

    # Wait match printout
    assert "[WAIT MATCH]" in out
    assert "id=0x123" in out
    U = out.upper()
    assert ("DATA=0102" in U) or ("DATA=[01 02]" in U)


def test_repl_watch_idempotent(monkeypatch, capsys):
    _scripted_input(monkeypatch, ["watch", "watch", "quit"])
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out

    # Must print that we're watching
    assert "Watching all frames." in out
    # Idempotent behavior: only one handler should be registered
    assert FakeClient.instances, "FakeClient instance not created"
    inst = FakeClient.instances[-1]
    assert len(inst.on_frame_handlers) == 1
    # The message may or may not include "Already watching.", so don't require it.


def test_repl_help_and_unknown(monkeypatch, capsys):
    _scripted_input(monkeypatch, ["help", "nonsense", "quit"])
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out
    # Accept either phrasing depending on the __doc__ text
    assert ("Interactive console" in out) or ("Minimal interactive console" in out)
    assert "Unknown command" in out


def test_repl_send_usage_and_errors(monkeypatch, capsys):
    # Missing data -> usage
    _scripted_input(monkeypatch, ["send 0x123", "quit"])
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Usage: send <id> <hex> [ext|std] [rtr]" in out

    # Bad id -> error
    _scripted_input(monkeypatch, ["send g123 00", "quit"])
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Invalid CAN ID 'g123'" in out

    # Bad data -> error
    _scripted_input(monkeypatch, ["send 0x123 zz", "quit"])
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Error parsing data bytes" in out


def test_repl_wait_errors_and_timeout(monkeypatch, capsys):
    # Bad d0 then bad d1 then timeout
    FakeClient.raise_timeout = True
    _scripted_input(
        monkeypatch,
        [
            "wait 0x123 gg",  # bad d0
            "wait 0x123 01 zz",  # bad d1
            "wait 0x123 01 02 0.01",  # timeout
            "quit",
        ],
    )
    code = cli.main(["--host", "127.0.0.1", "--port", "20001", "repl"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Invalid d0 'gg'" in out
    assert "Invalid d1 'zz'" in out
    assert "[WAIT TIMEOUT]" in out
