# caneth.client

Async client for Waveshare 2-CH-CAN-TO-ETH "transparent" CAN frames.

Protocol (per Waveshare docs)
-----------------------------
Each CAN frame is transported as **13 bytes** over TCP:

1. flags/DLC (1 byte)::
     bit7 (0x80) -> 1 = Extended (29-bit), 0 = Standard (11-bit)
     bit6 (0x40) -> 1 = Remote (RTR) frame, 0 = Data frame
     bits3..0    -> DLC (0..8)
2. CAN ID (4 bytes, big-endian)
3. Data (8 bytes, zero-padded; DLC is the number of valid bytes)

The device may batch multiple frames per TCP packet; this client reads a stream and
slices it into 13-byte frames accordingly.

## Classes

### CANFrame

Dataclass representing a single CAN frame observed or sent via the device.

**Constructor** `(self, can_id: 'int', data: 'bytes', dlc: 'int', extended: 'bool', rtr: 'bool', timestamp: 'float') -> None`

#### Methods


### WaveShareCANClient

High-level asyncio client for the Waveshare "2-CH-CAN-TO-ETH" transparent CAN bridge.

    Parameters:
        host: IP address of the device (or TCP endpoint proxying the device).
        port: TCP port of the CAN channel (e.g., 20001 for CAN1).
        reconnect_initial: Initial reconnect delay in seconds.
        reconnect_max: Maximum reconnect delay in seconds. Set to 0 to reconnect forever.
            reconnect_cap: When reconnect_max == 0, cap the backoff to this many seconds (default 60.0).
        name: Human-friendly name for logs and task names.

    Notes:
        - The client runs a background receive loop and will automatically reconnect
          if the TCP connection drops.
        - Use `on_frame` for global subscriptions and `register_callback` for exact
          (CAN ID, first byte, second byte) matches.
        - Use `wait_for` to await a single matching frame.

**Constructor** `(self, host: 'str', port: 'int', reconnect_initial: 'float' = 0.5, reconnect_max: 'float' = 10.0, reconnect_cap: 'float' = 60.0, name: 'str' = 'can1') -> 'None'`

#### Methods

- **close** `(self) -> 'None'`
  
  Stop tasks and close the TCP connection.

- **on_frame** `(self, callback: 'Callback') -> 'None'`
  
  Register a callback invoked for every received CAN frame.

        The callback can be synchronous or asynchronous.

- **register_callback** `(self, can_id: 'int', d0: 'Optional[int]' = None, d1: 'Optional[int]' = None, callback: 'Callback' = None) -> 'None'`
  
  Register a callback for a specific CAN ID and optionally the first one or two data bytes.

            Args:
                can_id: CAN identifier (11-bit or 29-bit numeric).
                d0: Optional first data byte (0..255). If None, match any first byte.
                d1: Optional second data byte (0..255). If provided, `d0` must also be provided.
                callback: Sync or async function taking a CANFrame.

- **send** `(self, can_id: 'int', data: 'Union[bytes, bytearray, List[int], Tuple[int, ...]]' = b'', *, extended: 'Optional[bool]' = None, rtr: 'bool' = False) -> 'None'`
  
  Send a CAN frame encoded in the Waveshare 13-byte format.

        Args:
            can_id: 11-bit or 29-bit numeric ID.
            data: Up to 8 bytes of payload (bytes-like or list/tuple of ints).
            extended: If None, choose True when can_id > 0x7FF, else False.
            rtr: Whether to send an RTR (remote) frame.

- **start** `(self) -> 'None'`
  
  Start the background connection manager & receive loop.

        This returns immediately; to wait for a successful connection,
        call `await wait_connected()`.

- **wait_connected** `(self, timeout: 'Optional[float]' = None) -> 'None'`
  
  Wait until the client is connected.

        Raises:
            asyncio.TimeoutError: if connection is not established in time.

- **wait_for** `(self, can_id: 'int', d0: 'Optional[int]' = None, d1: 'Optional[int]' = None, *, timeout: 'Optional[float]' = None, callback: 'Optional[Callback]' = None) -> 'CANFrame'`
  
  Wait for the next frame matching the given CAN ID and optional first two data bytes.

        Args:
            can_id: Required CAN identifier to match.
            d0: Optional first data byte to match (0..255).
            d1: Optional second data byte to match (0..255).
            timeout: Optional timeout in seconds.
            callback: Optional one-off callback to invoke when matched.

        Returns:
            The matching CANFrame.

        Raises:
            asyncio.TimeoutError: if the timeout elapses first.


### _Waiter

Internal helper for one-shot waiters registered by `wait_for`.

**Constructor** `(self, can_id: 'int', d0: 'Optional[int]', d1: 'Optional[int]', fut: 'asyncio.Future', callback: 'Optional[Callback]') -> None`

#### Methods
