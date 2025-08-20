import asyncio

import pytest
from caneth.client import WaveShareCANClient

from .helpers import build_frame

pytestmark = pytest.mark.asyncio


async def test_send_frame_encoding(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="send-enc")
    await client.start()
    await client.wait_connected(timeout=2.0)

    # Send a standard frame
    await client.send(0x123, [0x01, 0x02, 0x03, 0x04])
    raw = await asyncio.wait_for(state.recv_queue.get(), timeout=1.0)
    assert len(raw) == 13
    # Compare with locally built frame
    expected = build_frame(0x123, b"\x01\x02\x03\x04")
    assert raw == expected

    # Send extended frame explicitly
    await client.send(0x1234567, b"\xaa\xbb", extended=True)
    raw2 = await asyncio.wait_for(state.recv_queue.get(), timeout=1.0)
    expected2 = build_frame(0x1234567, b"\xaa\xbb", extended=True)
    assert raw2 == expected2

    await client.close()
