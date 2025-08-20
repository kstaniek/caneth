[![CI](https://github.com/kstaniek/caneth/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/kstaniek/caneth/actions/workflows/test.yml)
[![Lint](https://github.com/kstaniek/caneth/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/kstaniek/caneth/actions/workflows/lint.yml)
[![Type](https://github.com/kstaniek/caneth/actions/workflows/type.yml/badge.svg?branch=main)](https://github.com/kstaniek/caneth/actions/workflows/type.yml)
[![codecov](https://codecov.io/gh/kstaniek/caneth/branch/main/graph/badge.svg)](https://codecov.io/gh/kstaniek/caneth)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://kstaniek.github.io/caneth/)
[![PyPI version](https://img.shields.io/pypi/v/caneth.svg)](https://pypi.org/project/caneth/)
[![Python versions](https://img.shields.io/pypi/pyversions/caneth.svg)](https://pypi.org/project/caneth/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

# caneth

Asyncio CAN client for **Waveshare 2-CH-CAN-TO-ETH** devices.  
It implements the device's 13‑byte transparent CAN frame format (1 flag/DLC byte + 4‑byte CAN ID + 8 data bytes), supports **auto‑reconnect**, and provides:

- A **receive loop** with:
  - global observers (`on_frame`) and
  - precise filters (`register_callback(can_id, d0, d1)`).
- A **one‑shot awaitable** API (`wait_for`) to await the next matching frame.
- A **CLI** with `watch`, `send`, `wait`, and an interactive `repl`.

> Requires Python **3.9+**.

---

## Install (local dev)

```bash
# inside the repo root (contains pyproject.toml)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

---

## CLI

```bash
# Watch frames
caneth --host 192.168.0.7 --port 20001 watch

# Send a frame (standard ID 0x123, payload 01 02 03 04)
caneth --host 192.168.0.7 --port 20001 send --id 0x123 --data "01 02 03 04"

# Wait for a specific frame (ID 0x123, bytes 01 02) with 10s timeout
caneth --host 192.168.0.7 --port 20001 wait --id 0x123 --d0 0x01 --d1 0x02 --wait-timeout 10

# Interactive console (send, on, watch, wait, help, quit)
caneth --host 192.168.0.7 --port 20001 repl
```

If `caneth` is not on PATH, you can run the module directly:

```bash
python -m caneth.cli --host 192.168.0.7 --port 20001 watch
```

---

## Python API

### Import

```python
from caneth import WaveShareCANClient, CANFrame, parse_hex_bytes
```

### `class WaveShareCANClient(host: str, port: int, *, reconnect_initial=0.5, reconnect_max=10.0, reconnect_cap=60.0, name="can1")`

An asyncio TCP client that speaks the Waveshare transparent CAN protocol.

- **host** / **port**: IP and TCP port of the device's CAN channel (e.g., `20001` for CAN1).
- **reconnect_initial**: initial reconnect backoff in seconds.
- **reconnect_max**: maximum backoff in seconds. Set to `0` to reconnect forever.
- **reconnect_cap**: when `reconnect_max=0`, the delay is capped to this value (default **60.0s**).
- **name**: name used in logs/task names.

#### Methods

##### `await start() -> None`
Starts the background connection manager and receive loop. Returns immediately; use `wait_connected()` to wait for the first connection.

##### `await wait_connected(timeout: float | None = None) -> None`
Wait until the socket is connected (or raise `asyncio.TimeoutError`).

##### `await close() -> None`
Stops the background tasks and closes the socket.

##### `register_callback(can_id: int, d0: int | None = None, d1: int | None = None, callback: Callable[[CANFrame], Awaitable[None] | None]) -> None`
Register a callback for a specific **CAN ID** and optionally the **first one or two data bytes**.
- With only `can_id`, it triggers on *any* payload for that ID.
- With `d0`, it triggers when the first byte matches.
- With `d0` and `d1`, it triggers when both first bytes match.
- If you pass `d1`, you must also pass `d0`.

##### `on_frame(callback: Callable[[CANFrame], Awaitable[None] | None]) -> None`
Registers a callback invoked for **every** received frame (sync or async).

##### `await wait_for(can_id: int, d0: int | None = None, d1: int | None = None, *, timeout: float | None = None, callback: Callable[[CANFrame], Awaitable[None] | None] | None = None) -> CANFrame`
Waits for the **next** frame whose CAN ID matches and whose first one or two data bytes (if provided) match.
- Returns the matching `CANFrame`.
- Raises `asyncio.TimeoutError` on timeout.
- If `callback` is provided, it is invoked **once** when the match occurs.

##### `await send(can_id: int, data: bytes | list[int] | tuple[int, ...] = b"", *, extended: bool | None = None, rtr: bool = False) -> None`
Sends one CAN frame:
- `extended`: when `None`, it auto‑selects extended if `can_id > 0x7FF`.
- `data`: up to 8 bytes; you can pass bytes or a list/tuple of ints `0..255`.
- `rtr`: set `True` for RTR frames.
- The device format is encoded for you: `flags/DLC (1) + CAN ID (4, big-endian) + data (8, padded)`.

---

## Usage Examples

### 1) Observe every frame and send a couple

```python
import asyncio
from caneth import WaveShareCANClient

async def main():
    client = WaveShareCANClient("192.168.0.7", 20001, name="CAN1")

    # Print every frame received
    client.on_frame(lambda f: print("[RX]", f))

    await client.start()
    await client.wait_connected(timeout=10)

    # Send standard
    await client.send(0x123, [0x01, 0x02, 0x03, 0x04])
    # Send extended
    await client.send(0x12345678, b"\xDE\xAD\xBE\xEF", extended=True)

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()

asyncio.run(main())
```

### 2) Register a precise filter (ID + first two data bytes)

```python
import asyncio
from caneth import WaveShareCANClient, CANFrame

async def main():
    client = WaveShareCANClient("192.168.0.7", 20001, name="CAN1")

    def on_specific(f: CANFrame) -> None:
        print("[MATCH 0x123/01 02]", f)

    client.register_callback(0x123, 0x01, 0x02, on_specific)

    await client.start()
    await client.wait_connected(timeout=10)

    # keep running
    await asyncio.sleep(3600)

asyncio.run(main())
```

### 3) Wait for a frame once (optionally with a one-off callback)

```python
import asyncio
from caneth import WaveShareCANClient

async def main():
    client = WaveShareCANClient("192.168.0.7", 20001, name="CAN1")
    await client.start()
    await client.wait_connected(timeout=10)

    async def when_found(frame):
        print("[ONE-OFF CALLBACK]", frame)

    try:
        frame = await client.wait_for(0x123, d0=0x01, d1=0x02, timeout=5, callback=when_found)
        print("Received:", frame)
    except asyncio.TimeoutError:
        print("Timed out waiting for the frame")
    finally:
        await client.close()

asyncio.run(main())
```

### 4) Convert user-friendly hex strings to bytes

```python
from caneth import parse_hex_bytes

print(parse_hex_bytes("12 34 56"))         # b'\x12\x34\x56'
print(parse_hex_bytes("0x12,0xFF,0x00"))   # b'\x12\xFF\x00'
print(parse_hex_bytes("12-34-56"))         # b'\x12\x34\x56'
print(parse_hex_bytes("123456"))           # b'\x12\x34\x56'
```

---

## Protocol Notes (Waveshare transparent CAN)

- Each CAN frame is encoded as **13 bytes** over TCP:
  1. **flags/DLC** (1 byte):
     - bit7 (`0x80`): 1 = **Extended (29-bit)**, 0 = **Standard (11-bit)**
     - bit6 (`0x40`): 1 = **RTR**, 0 = **Data frame**
     - bits3..0 (`0x0F`): **DLC** (0..8) — number of valid data bytes
  2. **CAN ID** (4 bytes, **big-endian**)
  3. **Data** (8 bytes, zero-padded; DLC says how many are valid)
- The device may **batch multiple frames** into one TCP packet; this client reads a stream and slices it into 13‑byte chunks.
- The client auto‑chooses **extended** if `can_id > 0x7FF` unless you force it via `extended=True/False` in `send()`.

---

## Reconnect Behavior

- The client automatically reconnects on socket errors and EOF (set `reconnect_max=0` to retry forever; delay capped by `reconnect_cap`).
- Backoff grows from `reconnect_initial` up to `reconnect_max`.
- `wait_connected()` is useful after `start()` to wait for first connect.

---

## Threading & Concurrency

- Designed for **single asyncio event loop**; callbacks may be sync or async.
- `register_callback` and `on_frame` callbacks are invoked **serially** in the receive task; prefer light, non-blocking work (or spawn your own tasks).

---

## Troubleshooting

- **No frames?** Verify the device mode (TCP server vs client), IP/port, and that the Waveshare channel (e.g., CAN1 → port 20001) is enabled.
- **Extended IDs**: If you pass an extended ID but force `extended=False`, the device will still receive the 29‑bit ID value; ensure your CAN side expects standard vs extended correctly.
- **Permissions / firewalls**: Make sure the host firewall allows outbound TCP to the device port.

---

## Minimal Test Harness (optional)

If you don't have the device handy, you can simulate a server that pushes a single frame in the Waveshare format:

```python
import asyncio

def build_frame(can_id: int, data: bytes, extended: bool = None, rtr: bool = False) -> bytes:
    if extended is None:
        extended = can_id > 0x7FF
    dlc = len(data)
    b0 = (0x80 if extended else 0) | (0x40 if rtr else 0) | (dlc & 0x0F)
    buf = bytearray(13)
    buf[0] = b0
    buf[1:5] = can_id.to_bytes(4, "big")
    buf[5:5+dlc] = data
    return bytes(buf)

async def fake_server(reader, writer):
    # send ID=0x123 with data 01 02 03 04 after a short delay
    await asyncio.sleep(1)
    writer.write(build_frame(0x123, b"\x01\x02\x03\x04"))
    await writer.drain()
    await asyncio.sleep(1)
    writer.close()

async def main():
    server = await asyncio.start_server(fake_server, "127.0.0.1", 20001)
    print("Fake server on 127.0.0.1:20001 (Ctrl-C to quit)")
    async with server:
        await server.serve_forever()

asyncio.run(main())
```

Run the server in one terminal, then in another:

```bash
caneth --host 127.0.0.1 --port 20001 watch
# or:
caneth --host 127.0.0.1 --port 20001 wait --id 0x123 --d0 0x01 --d1 0x02 --wait-timeout 5
```

---

## License

MIT (see `pyproject.toml` classifiers or add a `LICENSE` file if you plan to distribute).

---

## Testing

This repo ships with a small pytest suite and a fake TCP server that emulates the Waveshare framing.

### 1) Install dev dependencies

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e . pytest pytest-asyncio
```

> Python **3.9+** is required. Pytest-asyncio is needed because the tests use async fixtures and async test functions.

### 2) (Recommended) Configure pytest for asyncio

Create a `pytest.ini` at the repo root (where `pyproject.toml` lives):

```ini
[pytest]
asyncio_mode = auto
```

This removes strict-mode warnings and ensures async fixtures work without extra markers.

### 3) Run the test suite

```bash
pytest -q
```

Run a single test file or a single test case:

```bash
pytest tests/test_wait_for.py -q
pytest tests/test_wait_for.py::test_wait_for_id_only -q
```

Stream `print()` output for debugging:

```bash
pytest -s tests/test_client_receive.py::test_on_frame_receives
```

(Optional) Coverage:

```bash
pip install pytest-cov
pytest --cov=caneth --cov-report=term-missing
```

### What the tests do

- **Unit helpers:** `tests/test_utils.py` checks `parse_hex_bytes`.
- **Client RX:** `tests/test_client_receive.py` ensures `on_frame` observers fire.
- **Callback registry:** `tests/test_register_callback.py` covers ID-only, ID+`d0`, and ID+`d0`+`d1` matchers.
- **Awaiting frames:** `tests/test_wait_for.py` covers `wait_for(...)` for ID-only and with first bytes.
- **Encoding:** `tests/test_send_encoding.py` validates the 13-byte Waveshare format written by `send(...)`.
- **CLI parsers:** `tests/test_cli_parsers.py` validates permissive hex/decimal parsing for IDs/bytes.

A fake in-process TCP server fixture (`ws_server`) is used to avoid real hardware.

### Common issues & fixes

- **`ModuleNotFoundError: No module named 'caneth'`**  
  Make sure you installed the package in editable mode from repo root:  
  `pip install -e .`

- **Async fixture warnings/errors (e.g., “async_generator object”, “strict mode”)**  
  Ensure `pytest-asyncio` is installed and `tests/conftest.py` uses `@pytest_asyncio.fixture`.  
  Prefer adding `pytest.ini` with `asyncio_mode = auto` (see step 2).

- **“attempted relative import with no known parent package”**  
  Make sure you are running `pytest` from the project root (so it discovers the package) and you have the current `tests/` tree. If you created your own helpers, avoid importing from `conftest.py`; put helpers in `tests/helpers.py` instead.

- **Old `conftest.py` errors like `NameError: send is not defined`**  
  Ensure you’re on the updated tests where the fixture returns a `SimpleNamespace` for state. Replace your `tests/conftest.py` with the one in this repo.

---

---

## CI, Docs & Publishing (GitHub Actions)

This repo includes three workflows under `.github/workflows/`:

1) **CI** — test matrix on Python 3.9–3.13 with coverage.
   - File: `.github/workflows/ci.yml`
   - Triggers on pushes and PRs.
   - Produces a `coverage.xml` artifact per Python version.
   - Optionally uploads coverage to Codecov if `CODECOV_TOKEN` secret is set.

2) **Docs** — builds API docs with **pdoc** and publishes to **GitHub Pages**.
   - File: `.github/workflows/docs.yml`
   - Triggers on pushes to `main`/`master` and manual runs.
   - Output is published to the repository’s GitHub Pages.
   - Enable Pages in **Settings → Pages**, or just run the workflow; it configures the Pages environment.

3) **Publish** — builds sdist/wheel and publishes to **PyPI** on version tags.
   - File: `.github/workflows/publish.yml`
   - Triggers on tags named like `v1.2.3`.
   - Uses **Trusted Publishing** if your PyPI project is configured for it (OIDC), otherwise set **Repository Secret** `PYPI_API_TOKEN`.
   - You can create an API token here: https://pypi.org/manage/account/token/

### Setup steps

- **Codecov (optional):** add a repository secret `CODECOV_TOKEN`.
- **PyPI (optional):** either enable **Trusted Publishing** on PyPI (recommended) or add `PYPI_API_TOKEN` as a repository secret.
- **GitHub Pages (Docs):** after the first successful run of `Docs` workflow, a Pages site URL will appear in the workflow summary. You can also configure it manually in Settings → Pages.

### Run locally

```bash
# Build docs locally
pip install pdoc .
pdoc -o site caneth
python -m http.server -d site 8000  # view at http://localhost:8000
```