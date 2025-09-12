"""
Async client for the Waveshare 2-CH-CAN-TO-ETH TCP framing (13-byte frames).

Frame format (exactly 13 bytes on the TCP stream):
- Byte 0:
    - bit7 (0x80): Extended ID flag (1 => 29-bit)
    - bit6 (0x40): RTR flag (1 => remote frame)
    - bits3..0: DLC (0..8)
    - other bits reserved 0
- Bytes 1..4: Big-endian CAN ID (29 or 11 bits placed in low bits)
- Bytes 5..12: Up to 8 data bytes; unused tail is zero padded

This module provides:
- `WaveShareCANClient`: asyncio TCP client with auto-reconnect
- `CANFrame`: typed container with `to_bytes()` and `from_bytes()`
- Register callbacks by (id), (id+d0) or (id+d0+d1)
- `wait_for(...)` to await a matching frame (optionally d0/d1) and optionally
  invoke a callback when it matches.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

__all__ = ["CANFrame", "WaveShareCANClient"]

# ----------------------------- Data structures ---------------------------------


@dataclass(slots=True)
class _TxItem:
    """
    One atomic unit for transmission.

    The `atomic` field controls frame ordering and interleaving guarantees:

    - If `atomic=True`, all frames in `frames` are sent together, without interleaving
      with frames from other transmissions. This guarantees that the sequence of frames
      is preserved, even across reconnections. If a reconnection occurs before all frames
      are sent, the entire atomic group will be retransmitted, ensuring ordering and atomicity.

    - If `atomic=False`, frames may be interleaved with other transmissions. There is no
      guarantee that the frames will be sent together or in order relative to other frames.
      During reconnection, only unsent frames will be retransmitted, and ordering may not
      be preserved.

    Use `atomic=True` when frame ordering and atomic delivery are required (e.g., for
    multi-frame transactions or protocols that require strict sequencing). Use `atomic=False`
    for independent frames where ordering and grouping are not critical.

    Attributes:
        frames: List of one or more 13-byte encoded frames to transmit.
        atomic: If True, frames are sent as an atomic group; if False, frames may be interleaved.
        can_id: Optional CAN ID metadata (for logging).
    """

    frames: list[bytes]  # one or more 13-byte encoded frames
    atomic: bool  # if True, must not be interleaved
    can_id: int | None = None  # optional metadata (for logging)


@dataclass(slots=True)
class CANFrame:
    """
    A single CAN frame as carried by the Waveshare 13-byte wire format.

    Attributes:
        can_id: Integer CAN identifier (11- or 29-bit).
        data: Payload (0..8 bytes).
        extended: True for 29-bit ID, False for 11-bit.
        rtr: True if RTR (remote) frame.
        dlc: Declared length (0..8). Normally equals len(data).
    """

    can_id: int
    data: bytes
    extended: bool
    rtr: bool
    dlc: int

    def __str__(self) -> str:
        id_fmt = f"0x{self.can_id:08X}" if self.extended else f"0x{self.can_id:03X}"
        data_hex = self.data.hex().upper()
        spaced = " ".join(data_hex[i : i + 2] for i in range(0, len(data_hex), 2))
        typ = "RTR" if self.rtr else "DATA"
        return f"CANFrame(id={id_fmt}, ext={int(self.extended)}, type={typ}, dlc={self.dlc}, data=[{spaced}])"

    # ------------ Encode / decode in Waveshare 13-byte format -------------

    @staticmethod
    def from_bytes(buf: bytes) -> CANFrame:
        """Decode a 13-byte buffer into a CANFrame."""
        if len(buf) != 13:
            raise ValueError("Waveshare frame must be exactly 13 bytes")
        b0 = buf[0]
        extended = bool(b0 & 0x80)
        rtr = bool(b0 & 0x40)
        dlc = b0 & 0x0F
        if dlc > 8:
            raise ValueError(f"Invalid DLC {dlc}")
        can_id = int.from_bytes(buf[1:5], "big", signed=False)
        data = bytes(buf[5 : 5 + dlc])
        return CANFrame(can_id=can_id, data=data, extended=extended, rtr=rtr, dlc=dlc)

    def to_bytes(self) -> bytes:
        """Encode this frame as a 13-byte buffer."""
        if not (0 <= self.dlc <= 8):
            raise ValueError("DLC must be 0..8")
        if self.dlc != len(self.data):
            raise ValueError("dlc must equal len(data)")
        b0 = (0x80 if self.extended else 0) | (0x40 if self.rtr else 0) | (self.dlc & 0x0F)
        out = bytearray(13)
        out[0] = b0
        out[1:5] = int(self.can_id).to_bytes(4, "big", signed=False)
        out[5 : 5 + self.dlc] = self.data
        return bytes(out)


# ----------------------------- Client ------------------------------------------

Callback = Callable[[CANFrame], Awaitable[None] | None]
CallbackKey = tuple[int, int | None, int | None]


@dataclass(slots=True)
class _Waiter:
    can_id: int
    d0: int | None
    d1: int | None
    fut: asyncio.Future[CANFrame]
    callback: Callback | None


class WaveShareCANClient:
    """
    Asyncio TCP client for Waveshare 2-CH-CAN-TO-ETH.

    Parameters:
        host: Device IP address.
        port: TCP port (e.g. 20001 for CAN1, 20002 for CAN2).
        reconnect_initial: Starting backoff delay (seconds).
        reconnect_max: Maximum backoff delay in seconds. Set to 0 to reconnect forever.
        reconnect_cap: When reconnect_max == 0 (retry forever), cap backoff to this many seconds (default 60).
        name: Logger name suffix.

        # Sending / buffering
        send_buffer_limit: Max number of frames kept in memory (default 1024).
        drop_oldest_on_full: If True (default), drop the oldest frame when buffer is full.
                             If False, `send()` will apply back-pressure and wait for space.

    Behavior:
    - Connects and reads fixed 13-byte frames in a loop; auto-reconnects on errors.
    - Global observers via `on_frame()`.
    - Specific callbacks via `register_callback(id[, d0[, d1]], cb)`.
    - `wait_for(id[, d0[, d1]], timeout=None, callback=None)`.
    - **Buffered send**: `send()` enqueues a frame; a background TX loop flushes when connected.
    """

    _TX_WAIT_TIMEOUT: float = 0.05  # Timeout for waiting on TX condition variable

    def __init__(
        self,
        host: str,
        port: int,
        reconnect_initial: float = 0.5,
        reconnect_max: float = 10.0,
        reconnect_cap: float = 60.0,
        *,
        send_buffer_limit: int = 1024,
        drop_oldest_on_full: bool = True,
        name: str = "can1",
    ) -> None:
        self.host = host
        self.port = port
        self.reconnect_initial = float(reconnect_initial)
        self.reconnect_max = float(reconnect_max)
        self.reconnect_cap = float(reconnect_cap)

        # Outgoing buffered frames (already encoded as 13-byte chunks)
        self._tx_buf: deque[_TxItem] = deque()
        self._tx_cv: asyncio.Condition = asyncio.Condition()
        self._send_buffer_limit = int(send_buffer_limit)
        self._drop_oldest_on_full = bool(drop_oldest_on_full)

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._mgr_task: asyncio.Task[None] | None = None
        self._rx_task: asyncio.Task[None] | None = None
        self._tx_task: asyncio.Task[None] | None = None

        self._connected = asyncio.Event()
        self._closed = asyncio.Event()

        self._callbacks: dict[CallbackKey, list[Callback]] = {}
        self._on_frame: list[Callback] = []
        self._waiters: list[_Waiter] = []

        self.log = logging.getLogger(f"caneth.client.{name}")
        if not self.log.handlers:
            # Default console handler if user did not configure logging
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            self.log.addHandler(handler)
        self.log.propagate = False  # Don't propagate to root logger
        self.log.setLevel(logging.INFO)

    # ----------------------------- Lifecycle -----------------------------

    async def start(self) -> None:
        """Start the background manager (connect/reconnect), RX loop and TX loop."""
        if self._mgr_task and not self._mgr_task.done():
            return
        self._closed.clear()
        self._mgr_task = asyncio.create_task(self._run(), name="caneth-run")
        # Start TX loop once; it waits on connection and buffer content.
        if not self._tx_task or self._tx_task.done():
            self._tx_task = asyncio.create_task(self._tx_loop(), name="caneth-tx")

    async def close(self) -> None:
        """Signal close and wait for background tasks. Safe to call multiple times."""
        self._closed.set()
        self._connected.clear()

        # Wake TX waiters
        async with self._tx_cv:
            self._tx_cv.notify_all()

        # Cancel RX/TX tasks
        if self._rx_task:
            self._rx_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rx_task
            self._rx_task = None

        if self._tx_task:
            self._tx_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tx_task
            self._tx_task = None

        await self._teardown_io()

        if self._mgr_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._mgr_task, timeout=1.0)
            self._mgr_task = None

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Wait until the TCP connection is established (or timeout)."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    # ------------------------------- API --------------------------------

    def buffer_size(self) -> int:
        """Current number of frames waiting in the send buffer."""
        return len(self._tx_buf)

    async def clear_buffer(self) -> None:
        """Drop all buffered frames (not yet sent)."""
        async with self._tx_cv:
            self._tx_buf.clear()
            self._tx_cv.notify_all()

    def on_frame(self, callback: Callback) -> None:
        """
        Register a global observer. Called for **every** received frame.

        The callback may be synchronous or async and receives a `CANFrame`.
        """
        self._on_frame.append(callback)

    def register_callback(
        self,
        can_id: int,
        d0: int | None = None,
        d1: int | None = None,
        callback: Callback | None = None,
    ) -> None:
        """
        Register a callback for a specific CAN ID and optionally the first one or two bytes.

        Matching levels (most specific wins first during iteration; duplicates are deduped):
        - (id, d0, d1)
        - (id, d0, None)
        - (id, None, None)

        Args:
            can_id: CAN identifier (11- or 29-bit).
            d0: Optional first data byte (0..255). If None, match any first byte.
            d1: Optional second data byte (0..255). If provided, `d0` must also be provided.
            callback: Function (sync or async) called with `CANFrame` when matched.
        """
        if callback is None:
            raise ValueError("callback is required")
        if d1 is not None and d0 is None:
            raise ValueError("d1 specified but d0 is None; provide d0 when specifying d1")
        if d0 is not None and not (0 <= int(d0) <= 255):
            raise ValueError("d0 must be in 0..255")
        if d1 is not None and not (0 <= int(d1) <= 255):
            raise ValueError("d1 must be in 0..255")

        d0i: int | None = None if d0 is None else int(d0)
        d1i: int | None = None if d1 is None else int(d1)
        key: CallbackKey = (int(can_id), d0i, d1i)
        self._callbacks.setdefault(key, []).append(callback)

    def unregister_callback(
        self,
        can_id: int,
        d0: int | None = None,
        d1: int | None = None,
        callback: Callback | None = None,
    ) -> int:
        """
        Unregister callbacks for the given (can_id, d0, d1) key.

        If `callback` is provided, only that function is removed.
        If `callback` is None, all callbacks for that key are removed.

        Returns:
            Number of callbacks removed.
        """
        key: CallbackKey = (int(can_id), None if d0 is None else int(d0), None if d1 is None else int(d1))
        lst = self._callbacks.get(key)
        if not lst:
            return 0
        removed = 0
        if callback is None:
            removed = len(lst)
            del self._callbacks[key]
            return removed
        # remove specific function(s)
        new_list = [cb for cb in lst if cb is not callback]
        removed = len(lst) - len(new_list)
        if removed:
            if new_list:
                self._callbacks[key] = new_list
            else:
                del self._callbacks[key]
        return removed

    def clear_callbacks(self) -> None:
        """Remove all specific (id/d0/d1) callbacks (does not affect `on_frame` observers)."""
        self._callbacks.clear()

    async def wait_for(
        self,
        can_id: int,
        *,
        d0: int | None = None,
        d1: int | None = None,
        timeout: float | None = None,
        callback: Callback | None = None,
    ) -> CANFrame:
        """
        Wait for the next frame that matches `can_id` and optionally first 1–2 bytes.

        Args:
            can_id: CAN identifier to match.
            d0: Optional first byte (0..255).
            d1: Optional second byte (0..255). If provided, `d0` must also be provided.
            timeout: Optional seconds to wait; raises `asyncio.TimeoutError` if exceeded.
            callback: Optional function (sync or async) to call when the frame arrives.

        Returns:
            The matching `CANFrame`.

        Raises:
            asyncio.TimeoutError: If `timeout` expires before a matching frame arrives.
        """
        if d1 is not None and d0 is None:
            raise ValueError("d1 specified but d0 is None; provide d0 when specifying d1")
        if d0 is not None and not (0 <= int(d0) <= 255):
            raise ValueError("d0 must be in 0..255")
        if d1 is not None and not (0 <= int(d1) <= 255):
            raise ValueError("d1 must be in 0..255")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CANFrame] = loop.create_future()
        waiter = _Waiter(
            can_id=int(can_id),
            d0=(None if d0 is None else int(d0)),
            d1=(None if d1 is None else int(d1)),
            fut=fut,
            callback=callback,
        )
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            # remove waiter on error/timeout
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)
            raise

    async def send(
        self,
        can_id: int,
        data: bytes | bytearray | Iterable[int] = b"",
        *,
        extended: bool | None = None,
        rtr: bool = False,
        wait_for_space: bool = False,
    ) -> None:
        """
        Enqueue a single frame (non-atomic item). TX loop will flush it when connected.
        For non-interleaved sequences, use send_batch(...) or the atomic(...) context.
        """
        # Explicit close => sending is an error
        if self._closed.is_set():
            raise RuntimeError("Client is closed")

        # Normalize payload
        payload = data if isinstance(data, bytes | bytearray) else bytes(data)
        if len(payload) > 8:
            raise ValueError("data length must be <= 8")

        if extended is None:
            extended = can_id > 0x7FF

        raw = self._encode_frame(can_id=int(can_id), data=payload, extended=bool(extended), rtr=bool(rtr))

        # Non-atomic item with a single frame (keeps backward compatibility)
        item = _TxItem(frames=[raw], atomic=False, can_id=int(can_id))
        await self._enqueue_item(item, wait_for_space=wait_for_space)

    async def send_batch(
        self,
        can_id: int,
        data: Iterable[bytes | bytearray | Iterable[int]],
        *,
        extended: bool | None = None,
        rtr: bool = False,
        wait_for_space: bool = False,
    ) -> None:
        """
        Enqueue several frames for the same CAN ID as an *atomic* batch.
        They will be sent back-to-back without interleaving.
        The batch remains atomic across reconnects (remaining frames re-queued together).
        """
        if self._closed.is_set():
            raise RuntimeError("Client is closed")

        data_list: list[bytes] = []
        for d in data:
            b = bytes(d) if not isinstance(d, bytes | bytearray) else bytes(d)
            if len(b) > 8:
                raise ValueError("data length must be <= 8")
            data_list.append(b)

        if extended is None:
            extended = can_id > 0x7FF

        frames = [self._encode_frame(can_id, b, extended=extended, rtr=rtr) for b in data_list]
        if not frames:
            return
        await self._enqueue_item(_TxItem(frames=frames, atomic=True, can_id=int(can_id)), wait_for_space=wait_for_space)

    # ---------------------------- Internals --------------------------------

    async def _run(self) -> None:
        """Background connection manager with auto-reconnect."""
        backoff = self.reconnect_initial
        while not self._closed.is_set():
            try:
                self.log.info("Connecting to %s:%s ...", self.host, self.port)
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self._reader, self._writer = reader, writer
                self._connected.set()
                self.log.info("Connected")

                # start rx loop
                self._rx_task = asyncio.create_task(self._read_loop(), name="caneth-rx")

                # Wait until rx loop finishes or we are closed
                done, _ = await asyncio.wait(
                    {self._rx_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # If rx loop ended (error or EOF), we'll fall through to reconnect
                for t in done:
                    exc = t.exception()
                    if exc:
                        raise exc
                # If ended cleanly, also drop through to reconnect unless closed
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.warning("Connection error: %s", e)
            finally:
                self._connected.clear()
                await self._teardown_io()

            if self._closed.is_set():
                break

            # Reconnect policy
            self.log.info("Reconnecting in %.2fs ...", backoff)
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=backoff)
                break  # closed while waiting
            except asyncio.TimeoutError:
                pass
            # Increase backoff: exponential up to 4x initial, then linear +1s
            next_backoff = backoff * 2 if backoff < self.reconnect_initial * 4 else backoff + 1.0
            if self.reconnect_max == 0:
                # Reconnect forever, but cap to reconnect_cap
                backoff = min(self.reconnect_cap, next_backoff)
            else:
                backoff = min(self.reconnect_max, next_backoff)

    async def _teardown_io(self) -> None:
        """Close and clear reader/writer and rx task."""
        if self._rx_task:
            self._rx_task.cancel()
            with contextlib.suppress(Exception):
                await self._rx_task
            self._rx_task = None

        writer = self._writer
        self._reader = None
        self._writer = None

        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        # Wake any senders waiting for space (so they can exit if closed)
        async with self._tx_cv:
            self._tx_cv.notify_all()

    async def _read_loop(self) -> None:
        """Read fixed 13-byte frames, decode, and dispatch. Exits on EOF."""
        reader = self._reader
        if reader is None:
            return
        while not self._closed.is_set():
            try:
                buf = await reader.readexactly(13)
            except asyncio.IncompleteReadError:
                self.log.info("EOF from device")
                break
            except Exception as e:
                self.log.warning("Read error: %s", e)
                break

            try:
                frame = CANFrame.from_bytes(buf)
            except Exception:
                self.log.exception("Failed to decode frame")
                continue

            try:
                await self._dispatch(frame)
            except Exception:
                self.log.exception("Error during dispatch")

    async def _tx_loop(self) -> None:
        """
        Background transmitter: flush buffered items when connected.

        - Pops one _TxItem at a time.
        - Sends all frames within the item back-to-back (preserving atomic batches).
        - On write error mid-item, re-queues the remaining frames as a single atomic item.
        - If writer disappears mid-loop, re-queues the whole item, clears _connected, and yields.
        """
        while not self._closed.is_set():
            # Wait for a connection
            await self._connected.wait()
            if self._closed.is_set():
                break

            try:
                while self._connected.is_set() and not self._closed.is_set():
                    item: _TxItem | None = None
                    async with self._tx_cv:
                        if self._tx_buf:
                            item = self._tx_buf.popleft()
                            # notify space for potential waiters
                            self._tx_cv.notify()
                        else:
                            # Wait for new data or periodic re-check (to notice disconnect/close)
                            try:
                                await asyncio.wait_for(self._tx_cv.wait(), timeout=self._TX_WAIT_TIMEOUT)
                            except asyncio.TimeoutError:
                                continue

                    if item is None:
                        continue

                    writer = self._writer
                    if writer is None:
                        # Lost connection; put the whole item back and hand off to reconnect.
                        async with self._tx_cv:
                            self._tx_buf.appendleft(item)
                            self._tx_cv.notify()
                        self._connected.clear()
                        # Yield to avoid a tight loop and let reconnect/teardown tasks run.
                        await asyncio.sleep(0)  # cooperative yield to avoid a tight re-loop
                        break

                    # Send all frames in this item contiguously
                    sent = 0  # how many frames were fully sent+drained
                    try:
                        data = b"".join(item.frames)
                        writer.write(data)
                        await writer.drain()
                        sent = len(item.frames)
                    except (asyncio.CancelledError, GeneratorExit):
                        raise  # don't swallow cancellations
                    except Exception as e:
                        self.log.warning("Write error: %s; will retry after reconnect", e)
                        # Re-queue the remaining frames atomically (starting from the first unsent)
                        remaining = item.frames[sent:]
                        if remaining:
                            async with self._tx_cv:
                                self._tx_buf.appendleft(
                                    _TxItem(frames=remaining, atomic=item.atomic, can_id=item.can_id)
                                )
                                self._tx_cv.notify()
                        self._connected.clear()  # hand control to reconnect manager
                        break

            except asyncio.CancelledError:
                break
            except Exception:
                self.log.exception("Unexpected error in TX loop")
                await asyncio.sleep(self._TX_WAIT_TIMEOUT)

    async def _dispatch(self, frame: CANFrame) -> None:
        """Run global observers, specific callbacks, and resolve waiters."""
        # 1) Global observers
        for cb in list(self._on_frame):
            try:
                result = cb(frame)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                self.log.exception("Error in on_frame callback")

        # 2) Specific callbacks with optional wildcards
        candidates: list[CallbackKey] = []
        candidates.append((frame.can_id, None, None))  # ID only
        if frame.dlc >= 1:
            candidates.append((frame.can_id, frame.data[0], None))  # ID + d0
        if frame.dlc >= 2:
            candidates.append((frame.can_id, frame.data[0], frame.data[1]))  # ID + d0 + d1

        seen: set[int] = set()
        for key in candidates:
            cbs = self._callbacks.get(key, [])
            for cb in cbs:
                ident = id(cb)
                if ident in seen:
                    continue
                seen.add(ident)
                try:
                    result = cb(frame)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    self.log.exception("Error in specific callback for %s", key)

        # 3) One-shot waiters
        if self._waiters:
            to_complete: list[_Waiter] = []
            for w in list(self._waiters):
                if frame.can_id != w.can_id:
                    continue
                if w.d0 is not None and (frame.dlc < 1 or frame.data[0] != w.d0):
                    continue
                if w.d1 is not None and (frame.dlc < 2 or frame.data[1] != w.d1):
                    continue
                to_complete.append(w)

            for w in to_complete:
                with contextlib.suppress(ValueError):
                    self._waiters.remove(w)
                if not w.fut.done():
                    w.fut.set_result(frame)
                if w.callback is not None:
                    try:
                        res = w.callback(frame)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        self.log.exception("Error in wait_for callback")

    def _encode_frame(self, can_id: int, data: bytes, *, extended: bool, rtr: bool) -> bytes:
        frame = CANFrame(can_id=int(can_id), data=data, extended=bool(extended), rtr=bool(rtr), dlc=len(data))
        return frame.to_bytes()

    async def _enqueue_item(self, item: _TxItem, *, wait_for_space: bool) -> None:
        async with self._tx_cv:
            if len(self._tx_buf) < self._send_buffer_limit:
                self._tx_buf.append(item)
                self._tx_cv.notify()
                return

            if self._drop_oldest_on_full:
                _ = self._tx_buf.popleft()  # drop oldest item
                self._tx_buf.append(item)
                self._tx_cv.notify()
                return

            if not wait_for_space:
                self.log.warning("TX buffer full; dropping item (can_id=%s, frames=%d)", item.can_id, len(item.frames))
                return

            # Back-pressure
            while len(self._tx_buf) >= self._send_buffer_limit and not self._closed.is_set():
                await self._tx_cv.wait()
            if not self._closed.is_set():
                self._tx_buf.append(item)
                self._tx_cv.notify()

    def atomic(
        self, can_id: int, *, extended: bool | None = None, rtr: bool = False, wait_for_space: bool = False
    ) -> _AtomicSender:
        """
        Usage:
            async with client.atomic(0x123) as a:
                await a.send(b"\x01")
                await a.send(b"\x02")
        Both frames are sent contiguously (no interleaving).
        """
        return _AtomicSender(self, can_id, extended=extended, rtr=rtr, wait_for_space=wait_for_space)


class _AtomicSender:
    def __init__(
        self, client: WaveShareCANClient, can_id: int, *, extended: bool | None, rtr: bool, wait_for_space: bool
    ):
        self._client = client
        self._can_id = int(can_id)
        self._extended = extended
        self._rtr = rtr
        self._wait = wait_for_space
        self._datas: list[bytes] = []

    async def send(self, data: bytes | bytearray | Iterable[int]) -> None:
        b = bytes(data) if not isinstance(data, bytes | bytearray) else bytes(data)
        if len(b) > 8:
            raise ValueError("data length must be <= 8")
        self._datas.append(b)

    async def __aenter__(self) -> _AtomicSender:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc is None and self._datas:
            await self._client.send_batch(
                self._can_id,
                self._datas,
                extended=self._extended,
                rtr=self._rtr,
                wait_for_space=self._wait,
            )
