import asyncio

import pytest
from caneth.client import WaveShareCANClient

pytestmark = pytest.mark.asyncio


async def test_writer_none_midloop_requeues_and_clears_connected(ws_server):
    """
    Simulate a race where _writer becomes None just as the TX loop is about to write.

    Expected:
      - The popped frame is re-queued (buffer_size stays 1).
      - Nothing is sent to the server.
      - _connected is cleared by the TX loop (so reconnect policy can run).
    """
    host, port, state = ws_server

    client = WaveShareCANClient(
        host,
        port,
        name="tx-writer-none",
        send_buffer_limit=8,
        drop_oldest_on_full=True,
    )

    # Start and connect normally
    await client.start()
    await client.wait_connected(timeout=2.0)
    await state.wait_client_connected(timeout=2.0)

    # Force the 'writer is None' branch:
    # Make the current writer disappear *before* enqueuing a frame,
    # so when TX loop wakes, it pops the frame and then sees writer None.
    client._writer = None  # intentional: simulate abrupt writer loss

    # Enqueue one frame; TX loop will pop it, see writer is None, requeue at head,
    # clear _connected, and break the inner loop.
    await client.send(0x777, b"\x01")

    # Give the TX loop a tick to process the buffer
    await asyncio.sleep(0.05)

    # The frame should still be in the buffer (re-queued), and connection should be marked lost
    assert client.buffer_size() == 1, "Frame should be re-queued after writer loss"
    assert not client._connected.is_set(), "_connected should be cleared by TX loop"

    # The server must not receive anything because no actual write happened
    with pytest.raises(asyncio.TimeoutError):
        await state.recv(timeout=0.2)

    await client.close()
