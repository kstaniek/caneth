# tests/conftest.py
import asyncio
import contextlib
from types import SimpleNamespace

import pytest_asyncio


@pytest_asyncio.fixture
async def ws_server():
    """
    Fake Waveshare CAN TCP server.
    Yields (host, port, state) where state has:
      - state.send(raw13): send a 13-byte frame to the client
      - state.recv_queue: frames written by the client (13-byte chunks)
      - state.connected: asyncio.Event set when a client connects
      - state.close(): async close server
    """
    recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
    connected = asyncio.Event()
    client_writer = {"w": None}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_writer["w"] = writer
        connected.set()
        try:
            while True:
                data = await reader.readexactly(13)
                # capture what the client sent (for send-encoding test)
                await recv_queue.put(data)
                # **echo** back so the client’s RX loop always sees a frame
                writer.write(data)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            client_writer["w"] = None

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    host, port = server.sockets[0].getsockname()[:2]

    async def send(raw: bytes):
        assert len(raw) == 13
        await connected.wait()
        w = client_writer["w"]
        if w is None:
            await asyncio.sleep(0.05)
            w = client_writer["w"]
        assert w is not None, "No client connected"
        w.write(raw)
        await w.drain()

    async def aclose():
        server.close()
        await server.wait_closed()

    state = SimpleNamespace(send=send, recv_queue=recv_queue, connected=connected, close=aclose)

    try:
        yield host, port, state
    finally:
        await aclose()
