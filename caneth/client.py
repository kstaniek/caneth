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
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from typing import (
    Callable,
    Optional,
)

__all__ = ["CANFrame", "WaveShareCANClient"]

# ----------------------------- Data structures ---------------------------------


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
CallbackKey = tuple[int, Optional[int], Optional[int]]


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

    Behavior:
    - Connects and reads fixed 13-byte frames in a loop.
    - Automatically reconnects on EOF or socket errors (see backoff knobs).
    - `on_frame()` registers global observers.
    - `register_callback(id[, d0[, d1]], cb)` registers matchers by ID and optional first bytes.
    - `wait_for(id[, d0[, d1]], timeout=None, callback=None)` awaits a matching frame and
      optionally calls `callback(frame)` when it arrives.
    """

    def __init__(
        self,
        host: str,
        port: int,
        reconnect_initial: float = 0.5,
        reconnect_max: float = 10.0,
        reconnect_cap: float = 60.0,
        name: str = "can1",
    ) -> None:
        self.host = host
        self.port = port
        self.reconnect_initial = float(reconnect_initial)
        self.reconnect_max = float(reconnect_max)
        self.reconnect_cap = float(reconnect_cap)

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self._rx_task: asyncio.Task[None] | None = None

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
        """Start the background connection manager task."""
        if self._task and not self._task.done():
            return
        self._closed.clear()
        self._task = asyncio.create_task(self._run(), name="caneth-run")

    async def close(self) -> None:
        """
        Signal close and wait for background tasks.
        Safe to call multiple times.
        """
        self._closed.set()
        self._connected.clear()

        if self._rx_task:
            self._rx_task.cancel()
            # swallow the cancellation from the rx task
            with contextlib.suppress(asyncio.CancelledError):
                await self._rx_task
            self._rx_task = None

        await self._teardown_io()

        if self._task:
            # don't let close() raise if the run task is already ending
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=1.0)
            self._task = None

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Wait until the TCP connection is established (or timeout)."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    # ------------------------------- API --------------------------------

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
    ) -> None:
        """
        Send a single frame.

        Args:
            can_id: Integer CAN ID.
            data: Byte-like payload (0..8 bytes). Iterable[int] accepted and packed.
            extended: Force extended (29-bit) or standard (11-bit). If None, inferred from `can_id > 0x7FF`.
            rtr: Remote frame flag.
        """
        payload = bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
        if len(payload) > 8:
            raise ValueError("data length must be <= 8")
        if extended is None:
            extended = can_id > 0x7FF
        frame = CANFrame(can_id=int(can_id), data=payload, extended=bool(extended), rtr=bool(rtr), dlc=len(payload))
        raw = frame.to_bytes()

        writer = self._writer
        if writer is None:
            raise RuntimeError("Not connected")
        writer.write(raw)
        await writer.drain()

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

    async def _read_loop(self) -> None:
        """
        Read fixed 13-byte frames, decode, and dispatch.

        Exits on EOF or IncompleteReadError.
        """
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
        # ID-only
        candidates.append((frame.can_id, None, None))
        # ID + d0
        if frame.dlc >= 1:
            candidates.append((frame.can_id, frame.data[0], None))
        # ID + d0 + d1
        if frame.dlc >= 2:
            candidates.append((frame.can_id, frame.data[0], frame.data[1]))

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
                # remove from list first to avoid race
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
