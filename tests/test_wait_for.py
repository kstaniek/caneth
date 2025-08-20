import asyncio

import pytest
from caneth.client import WaveShareCANClient

from .conftest import build_frame

pytestmark = pytest.mark.asyncio


async def test_wait_for_id_only(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="wait-id")
    await client.start()
    await client.wait_connected(timeout=2.0)

    async def send_later():
        await asyncio.sleep(0.05)
        await state.send(build_frame(0x321, b"\x11\x22"))

    asyncio.create_task(send_later())

    frame = await asyncio.wait_for(client.wait_for(0x321), timeout=1.0)
    assert frame.can_id == 0x321
    await client.close()


async def test_wait_for_id_d0_d1(ws_server):
    host, port, state = ws_server
    client = WaveShareCANClient(host, port, name="wait-id-d0-d1")
    await client.start()
    await client.wait_connected(timeout=2.0)

    async def send_later():
        await asyncio.sleep(0.05)
        await state.send(build_frame(0x777, b"\x01\x02\x03"))

    asyncio.create_task(send_later())

    frame = await asyncio.wait_for(client.wait_for(0x777, d0=0x01, d1=0x02), timeout=1.0)
    assert frame.data[:2] == b"\x01\x02"
    await client.close()
