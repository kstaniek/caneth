import pytest
from caneth.client import WaveShareCANClient

from .conftest import build_frame

pytestmark = pytest.mark.asyncio


async def test_send_frame_encoding(ws_server):
    host, port, state = ws_server

    client = WaveShareCANClient(host, port, name="test-send-encode")
    await client.start()
    await client.wait_connected(timeout=2.0)

    # Ensure the server side has accepted and tracked the connection
    await state.wait_client_connected(timeout=2.0)

    # Send a simple std-id frame with 3 bytes
    can_id = 0x123
    data = bytes([0x01, 0x02, 0x03])
    await client.send(can_id, data)

    # Server should receive exactly one 13-byte chunk; use a timeout to avoid hanging
    got = await state.recv(timeout=2.0)
    assert isinstance(got, (bytes | bytearray)) and len(got) == 13

    # Expected wire representation
    expected = build_frame(can_id, data, extended=False)
    assert got == expected

    await client.close()
