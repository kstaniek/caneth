import asyncio
import contextlib

import pytest_asyncio


# ---- Waveshare 13-byte frame builder (used by tests) -------------------------
def build_frame(can_id: int, data: bytes = b"", *, extended: bool | None = None, rtr: bool = False) -> bytes:
    """
    Build a 13-byte Waveshare/TCP frame:
      b0: [ext:1][rtr:1][res:4][dlc:4]
      b1..b4: CAN ID (big-endian, 32-bit)
      b5..b12: data (0..8), zero-padded
    """
    if extended is None:
        extended = can_id > 0x7FF
    if len(data) > 8:
        raise ValueError("data length must be <= 8")
    dlc = len(data)
    b0 = (0x80 if extended else 0) | (0x40 if rtr else 0) | (dlc & 0x0F)
    buf = bytearray(13)
    buf[0] = b0
    buf[1:5] = int(can_id).to_bytes(4, "big", signed=False)
    buf[5 : 5 + dlc] = data
    return bytes(buf)


# ---- Test server state object -------------------------------------------------
class State:
    """
    Holds the most recent client connection and a queue of 13-byte frames
    received from the client.

      - await state.wait_client_connected(timeout)
      - await state.send(bytes)          -> server->client
      - await state.recv(timeout=None)   -> next 13-byte chunk from client
      - await state.close_client()       -> drop connection
    """

    def __init__(self) -> None:
        self._writers: list[asyncio.StreamWriter] = []
        self._rx: asyncio.Queue[bytes] = asyncio.Queue()
        self._client_connected = asyncio.Event()

    # called by the server per connection
    def _track(self, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        self._client_connected.set()

    async def wait_client_connected(self, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._client_connected.wait(), timeout)

    async def _reader_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.readexactly(13)  # fixed-size protocol
                await self._rx.put(chunk)
        except asyncio.IncompleteReadError:
            pass  # client closed
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            with contextlib.suppress(ValueError):
                self._writers.remove(writer)

    async def send(self, data: bytes) -> None:
        """Send raw bytes to the most recent client connection."""
        if not self._writers:
            raise RuntimeError("No client connected")
        w = self._writers[-1]
        w.write(data)
        await w.drain()

    async def close_client(self) -> None:
        """Close the most recent client connection from the server side."""
        if not self._writers:
            return
        w = self._writers[-1]
        with contextlib.suppress(Exception):
            w.close()
            await w.wait_closed()

    async def recv(self, timeout: float | None = None) -> bytes:
        """Wait for the next 13-byte chunk received from client."""
        if timeout is None:
            return await self._rx.get()
        return await asyncio.wait_for(self._rx.get(), timeout)


# ---- Fixture: ws_server -------------------------------------------------------
@pytest_asyncio.fixture
async def ws_server(unused_tcp_port_factory):
    """
    Async TCP server fixture.

    Yields:
        (host, port, state)
    """
    host = "127.0.0.1"
    port = unused_tcp_port_factory()
    state = State()

    async def _client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        state._track(writer)
        await state._reader_loop(reader, writer)

    server = await asyncio.start_server(_client_handler, host, port)
    try:
        yield host, port, state
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
        for w in list(state._writers):
            with contextlib.suppress(Exception):
                w.close()
                await w.wait_closed()
