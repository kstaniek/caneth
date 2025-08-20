# tests/test_client_receive.py
import asyncio

import pytest
from caneth.client import CANFrame, WaveShareCANClient

# If you still have helpers.py, you can keep it for other tests;
# this test drives RX via server sends, so no helper needed here.

pytestmark = pytest.mark.asyncio


async def test_on_frame_receives(ws_server):
    host, port, state = ws_server

    client = WaveShareCANClient(host, port, name="test-on-frame")
    await client.start()
    await client.wait_connected(timeout=2.0)
    await asyncio.sleep(0.02)  # let RX loop spin up

    # Register waiter FIRST, then send
    t = asyncio.create_task(client.wait_for(0x123, timeout=1.5))
    await asyncio.sleep(0)  # yield so waiter is installed

    await state.send(_build_frame(0x123, b"\x01\x02\x03"))

    frame = await asyncio.wait_for(t, timeout=2.0)

    assert isinstance(frame, CANFrame)
    assert frame.can_id == 0x123
    assert frame.data == b"\x01\x02\x03"
    assert frame.dlc == 3
    assert frame.extended is False

    await client.close()


async def test_on_frame_multiple_frames(ws_server):
    """Install all waiters first; then push frames with tiny spacing."""
    host, port, state = ws_server

    client = WaveShareCANClient(host, port, name="test-on-frame-multi")
    await client.start()
    await client.wait_connected(timeout=2.0)
    await asyncio.sleep(0.02)

    # Create all waiters up-front (prevents race)
    t1 = asyncio.create_task(client.wait_for(0x100, timeout=1.0))
    t2 = asyncio.create_task(client.wait_for(0x101, timeout=1.0))
    t3 = asyncio.create_task(client.wait_for(0x102, timeout=1.0))
    await asyncio.sleep(0)  # let tasks register

    # Now send frames from the server side (tiny gaps help CI determinism)
    async def send_sequence():
        await asyncio.sleep(0.02)
        await state.send(_build_frame(0x100, b"\xaa"))
        await asyncio.sleep(0.02)
        await state.send(_build_frame(0x101, b"\xbb\xcc"))
        await asyncio.sleep(0.02)
        await state.send(_build_frame(0x102, b""))

    asyncio.create_task(send_sequence())

    f1, f2, f3 = await asyncio.gather(t1, t2, t3)

    assert (f1.can_id, f1.data) == (0x100, b"\xaa")
    assert (f2.can_id, f2.data) == (0x101, b"\xbb\xcc")
    assert (f3.can_id, f3.data) == (0x102, b"")

    await client.close()


# Minimal local frame builder to avoid extra imports
def _build_frame(can_id: int, data: bytes, *, extended: bool | None = None, rtr: bool = False) -> bytes:
    if extended is None:
        extended = can_id > 0x7FF
    dlc = len(data)
    assert 0 <= dlc <= 8
    b0 = (0x80 if extended else 0) | (0x40 if rtr else 0) | (dlc & 0x0F)
    buf = bytearray(13)
    buf[0] = b0
    buf[1:5] = int(can_id).to_bytes(4, "big", signed=False)
    buf[5 : 5 + dlc] = data
    return bytes(buf)
