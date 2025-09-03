import asyncio

import pytest
from caneth.client import WaveShareCANClient

from .conftest import build_frame

pytestmark = pytest.mark.asyncio


async def test_enqueue_before_start_flushes_on_connect(ws_server):
    """
    Frames enqueued before starting/connecting should flush on connect in FIFO order.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(host, port, name="buf-prestart")
    # Enqueue before starting the client
    await client.send(0x100, b"\x01")
    await client.send(0x101, b"\x02\x03")
    assert client.buffer_size() == 2

    # Start + connect; server should receive both frames
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    got1 = await state.recv(timeout=2.0)
    got2 = await state.recv(timeout=2.0)

    assert got1 == build_frame(0x100, b"\x01", extended=False)
    assert got2 == build_frame(0x101, b"\x02\x03", extended=False)
    await client.close()


async def test_drop_oldest_when_full(ws_server):
    """
    With drop_oldest_on_full=True and buffer limit N, the oldest frame is dropped
    when enqueuing beyond the limit; only the newest N are delivered.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(
        host,
        port,
        name="buf-drop-oldest",
        send_buffer_limit=2,
        drop_oldest_on_full=True,
    )

    # Enqueue 3 frames with limit=2 — expect to drop the first (0x200)
    await client.send(0x200, b"\xaa")
    await client.send(0x201, b"\xbb")
    await client.send(0x202, b"\xcc")
    assert client.buffer_size() == 2  # only last 2 kept

    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    got1 = await state.recv(timeout=2.0)  # should be 0x201
    got2 = await state.recv(timeout=2.0)  # then 0x202

    assert got1 == build_frame(0x201, b"\xbb", extended=False)
    assert got2 == build_frame(0x202, b"\xcc", extended=False)

    # No extra frames should arrive
    with pytest.raises(asyncio.TimeoutError):
        await state.recv(timeout=0.2)

    await client.close()


async def test_backpressure_wait_for_space(ws_server):
    """
    With drop_oldest_on_full=False and wait_for_space=True, a sender blocks
    until the buffer has room, preserving all frames and order.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(
        host,
        port,
        name="buf-backpressure",
        send_buffer_limit=1,
        drop_oldest_on_full=False,
    )

    # Fill the buffer with one frame
    await client.send(0x300, b"\x01")  # buffer now full

    # Start a second send that must wait for space
    send_done = asyncio.Event()

    async def _blocked_send():
        await client.send(0x301, b"\x02", wait_for_space=True)
        send_done.set()

    task = asyncio.create_task(_blocked_send())

    # Make sure it's blocked (no connection yet to drain the buffer)
    await asyncio.sleep(0.05)
    assert not send_done.is_set()

    # Now connect; buffer will flush and unblock the second send
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    # First frame out
    got1 = await state.recv(timeout=2.0)
    assert got1 == build_frame(0x300, b"\x01", extended=False)

    # Second sender should complete once space is freed and the frame is enqueued
    await asyncio.wait_for(send_done.wait(), timeout=1.0)

    # And then second frame out
    got2 = await state.recv(timeout=2.0)
    assert got2 == build_frame(0x301, b"\x02", extended=False)

    await client.close()
    await asyncio.gather(task, return_exceptions=True)


async def test_clear_buffer_drops_pending(ws_server):
    """
    clear_buffer() removes any queued frames so nothing gets sent on connect.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(host, port, name="buf-clear")
    await client.send(0x400, b"\xde\xad")
    await client.send(0x401, b"\xbe\xef")
    assert client.buffer_size() == 2

    # Clear and verify
    await client.clear_buffer()
    assert client.buffer_size() == 0

    # Start and connect; server should not receive anything
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    with pytest.raises(asyncio.TimeoutError):
        await state.recv(timeout=0.2)

    await client.close()
