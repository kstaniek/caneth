import asyncio

import pytest
from caneth.client import WaveShareCANClient

pytestmark = pytest.mark.asyncio


async def test_reconnect_after_server_close(ws_server):
    host, port, state = ws_server

    c = WaveShareCANClient(
        host,
        port,
        reconnect_initial=0.05,
        reconnect_max=0.2,
        name="reconnect-test",
    )
    await c.start()
    await c.wait_connected(timeout=2.0)

    # If the fixture can't close the server-side socket, skip gracefully.
    close_fn = getattr(state, "close_client", None)
    if close_fn is None:
        pytest.skip("ws_server fixture lacks close_client(); skipping reconnect test")

    # Drop the connection from server side to trigger client reconnect.
    await close_fn()

    # Give the client time to observe EOF and reconnect.
    await asyncio.sleep(0.3)

    # Should reconnect; wait for the connected event again.
    await c.wait_connected(timeout=2.0)

    await c.close()
