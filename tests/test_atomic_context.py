import asyncio

import pytest
from caneth.client import WaveShareCANClient

from .conftest import build_frame

pytestmark = pytest.mark.asyncio


async def test_atomic_context_no_interleave(ws_server):
    """
    Frames added within `async with client.atomic(can_id)` must be sent
    back-to-back (no interleaving), relative to other frames.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(
        host,
        port,
        name="atomic-no-interleave",
        send_buffer_limit=16,
        drop_oldest_on_full=True,
    )

    # Enqueue something BEFORE the batch
    await client.send(0x100, b"\xa0")

    # Start & connect
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    # Create an atomic batch for a different CAN ID
    async with client.atomic(0x222) as a:
        await a.send(b"\x01")
        await a.send(b"\x02")
        await a.send(b"\x03")

    # Enqueue something AFTER the batch
    await client.send(0x100, b"\xa1")

    # Expect order: A0, [batch 01, 02, 03 contiguous], A1
    got1 = await state.recv(timeout=2.0)
    got2 = await state.recv(timeout=2.0)
    got3 = await state.recv(timeout=2.0)
    got4 = await state.recv(timeout=2.0)
    got5 = await state.recv(timeout=2.0)

    assert got1 == build_frame(0x100, b"\xa0", extended=False)

    # Batch frames contiguous, same CAN ID
    assert got2 == build_frame(0x222, b"\x01", extended=False)
    assert got3 == build_frame(0x222, b"\x02", extended=False)
    assert got4 == build_frame(0x222, b"\x03", extended=False)

    assert got5 == build_frame(0x100, b"\xa1", extended=False)

    # No more frames
    with pytest.raises(asyncio.TimeoutError):
        await state.recv(timeout=0.2)

    await client.close()


async def test_atomic_context_mid_batch_disconnect(ws_server):
    """
    If the connection drops mid-batch, the remaining frames of that atomic
    batch must be re-queued together and delivered contiguously after reconnect.
    """
    host, port, state = ws_server

    client = WaveShareCANClient(
        host,
        port,
        name="atomic-mid-drop",
        send_buffer_limit=16,
        drop_oldest_on_full=True,
    )

    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    # Enqueue an atomic batch of three frames
    async with client.atomic(0x333) as a:
        await a.send(b"\x10")
        await a.send(b"\x11")
        await a.send(b"\x12")

    # Receive the first frame of the batch
    first = await state.recv(timeout=2.0)
    assert first == build_frame(0x333, b"\x10", extended=False)

    # Simulate mid-batch disconnect: close the client's writer transport.
    # This will cause the next write/drain to fail and trigger the TX loop's
    # requeue of the remaining frames as a single atomic item.
    w = client._writer  # type: ignore[attr-defined]
    if w is not None:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass

    # The client should reconnect automatically
    await state.wait_client_connected(timeout=2.0)

    # Remaining two frames must arrive contiguously and in order
    rem1 = await state.recv(timeout=2.0)
    rem2 = await state.recv(timeout=2.0)
    assert rem1 == build_frame(0x333, b"\x11", extended=False)
    assert rem2 == build_frame(0x333, b"\x12", extended=False)

    # No extras
    with pytest.raises(asyncio.TimeoutError):
        await state.recv(timeout=0.2)

    await client.close()
