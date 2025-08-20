import asyncio

import pytest
from caneth.client import WaveShareCANClient

from .conftest import build_frame

pytestmark = pytest.mark.asyncio


async def test_callbacks_id_only(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="cb-id-only")
    hit = asyncio.Event()

    def cb(f):
        hit.set()

    client.register_callback(0x1E5D, callback=cb)
    await client.start()
    await client.wait_connected(timeout=2.0)

    await state.send(build_frame(0x1E5D, b"\xaa\xbb"))
    await asyncio.wait_for(hit.wait(), timeout=2.0)
    await client.close()


async def test_callbacks_id_d0(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="cb-id-d0")
    hit = asyncio.Event()
    client.register_callback(0x123, 0xAA, callback=lambda f: hit.set())
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.send(build_frame(0x123, b"\xaa\x99"))
    await asyncio.wait_for(hit.wait(), timeout=2.0)
    await client.close()


async def test_callbacks_id_d0_d1(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="cb-id-d0-d1")
    hit = asyncio.Event()
    client.register_callback(0x123, 0xAA, 0xBB, callback=lambda f: hit.set())
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.send(build_frame(0x123, b"\xaa\xbb\xcc"))
    await asyncio.wait_for(hit.wait(), timeout=2.0)
    await client.close()
