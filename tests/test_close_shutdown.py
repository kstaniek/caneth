import pytest
from caneth.client import WaveShareCANClient

pytestmark = pytest.mark.asyncio


async def test_close_shuts_down_cleanly(ws_server):
    host, port, _ = ws_server

    client = WaveShareCANClient(host, port, name="shutdown")
    await client.start()
    await client.wait_connected(timeout=2.0)

    # Close should cancel rx task and tear down IO without raising.
    await client.close()

    # Subsequent close() calls should be no-ops and not raise.
    await client.close()

    # API should now report not connected; sending should fail.
    with pytest.raises(RuntimeError):
        await client.send(0x123, b"\x01")
