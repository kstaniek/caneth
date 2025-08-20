"""
Command Line Interface for caneth.

Commands:
  - watch: print all received frames
  - send:  send a single frame
  - wait:  wait for (and print) a matching frame
  - repl:  interactive shell (watch, send, on, wait, unwatch, list, help, quit)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import suppress

from .client import CANFrame, WaveShareCANClient
from .utils import parse_hex_bytes


def _parse_can_id(s: str) -> int:
    """
    Parse CAN ID from decimal or hex. Accepts forms like: 291, 0x123, or 123 (hex).
    """
    s = s.strip()
    try:
        return int(s, 0)
    except ValueError as e:
        if re.fullmatch(r"[0-9A-Fa-f]+", s):
            return int(s, 16)
        raise ValueError(f"Invalid CAN ID '{s}'. Use decimal (e.g., 291) or hex (e.g., 0x123 or 123).") from e


def _parse_byte(s: str, label: str = "byte") -> int:
    """
    Parse a single byte from decimal or hex (0..255). Accepts '255', '0xFF', 'ff'.
    """
    s = s.strip()
    try:
        val = int(s, 0)
    except ValueError as e:
        if re.fullmatch(r"[0-9A-Fa-f]{1,2}", s):
            val = int(s, 16)
        else:
            raise ValueError(f"Invalid {label} '{s}'. Use decimal (0..255) or hex (e.g., 0xAB or AB).") from e
    if not (0 <= val <= 255):
        raise ValueError(f"{label} out of range (0..255): {val}")
    return val


async def _cmd_watch(args: argparse.Namespace) -> None:
    """Connect and print all received frames until Ctrl-C."""
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    client = WaveShareCANClient(args.host, args.port, name=f"{args.host}:{args.port}")
    client.on_frame(lambda f: print(f"[RX] {f}"))
    await client.start()
    await client.wait_connected(timeout=args.timeout)
    print(f"Connected to {args.host}:{args.port}. Watching for frames (Ctrl-C to quit)...")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()


async def _cmd_send(args: argparse.Namespace) -> None:
    """Send a single frame and exit."""
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    client = WaveShareCANClient(args.host, args.port, name=f"{args.host}:{args.port}")
    await client.start()
    await client.wait_connected(timeout=args.timeout)

    try:
        can_id = _parse_can_id(args.id)
    except ValueError as e:
        print(f"Error: {e}")
        await client.close()
        return

    try:
        data = parse_hex_bytes(args.data) if args.data else b""
    except Exception as e:
        print(f"Error parsing data bytes: {e}")
        await client.close()
        return

    await client.send(can_id, data, extended=args.extended, rtr=args.rtr)
    print(
        f"Sent frame id=0x{can_id:X}, "
        f"ext={int(args.extended) if args.extended is not None else int(can_id > 0x7FF)}, "
        f"rtr={args.rtr}, data={data.hex().upper()}"
    )
    await client.close()


async def _cmd_wait(args: argparse.Namespace) -> None:
    """Wait for a specific frame (optionally d0/d1) and print it or timeout."""
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    client = WaveShareCANClient(args.host, args.port, name=f"{args.host}:{args.port}")
    await client.start()
    await client.wait_connected(timeout=args.timeout)

    # Optional bytes
    if args.d0 is not None:
        try:
            d0 = _parse_byte(args.d0, "d0")
        except ValueError as e:
            print(f"Error: {e}")
            await client.close()
            return
    else:
        d0 = None

    if args.d1 is not None:
        try:
            d1 = _parse_byte(args.d1, "d1")
        except ValueError as e:
            print(f"Error: {e}")
            await client.close()
            return
    else:
        d1 = None

    # CAN ID
    try:
        can_id = _parse_can_id(args.id)
    except ValueError as e:
        print(f"Error: {e}")
        await client.close()
        return

    # Wait
    try:
        frame = await client.wait_for(can_id, d0=d0, d1=d1, timeout=args.wait_timeout)
        print("Matched:", frame)
    except (asyncio.TimeoutError, TimeoutError):
        print("Timeout waiting for frame")
    finally:
        await client.close()


async def _cmd_repl(args: argparse.Namespace) -> None:
    """
    Minimal interactive console.

    Commands:
      send <id> <hex> [ext|std] [rtr]
      on <id> [d0] [d1]     # register a matcher (by ID, or ID+d0, or ID+d0+d1)
      unwatch <id|watch|all>
                            # unwatch by numeric ID, or stop global watch, or clear all
      list                  # show active watchers
      watch                 # print all frames (toggleable)
      wait <id> [d0] [d1] [timeout]
      help
      quit/exit
    """
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    client = WaveShareCANClient(args.host, args.port, name=f"{args.host}:{args.port}")
    await client.start()
    await client.wait_connected(timeout=args.timeout)
    print(f"Connected to {args.host}:{args.port}. Type 'help' for commands.")

    # --- Global watch (on_frame) toggle via flag ---
    watching = False

    def _global_watcher(frame: CANFrame) -> None:
        if watching:
            print(f"[RX] {frame}")

    # We register the global watcher lazily (only when 'watch' is first used)
    global_watcher_registered = False

    # --- Specific matchers registry: id -> (can_id, d0, d1, callback, active) ---
    next_watch_id = 1
    watches: dict[int, tuple[int, int | None, int | None, Callable[[CANFrame], None], bool]] = {}

    def _fmt_filter(can_id: int, d0: int | None, d1: int | None) -> str:
        filt = f"id=0x{can_id:X}"
        if d0 is not None:
            if d1 is not None:
                filt += f" d0={d0:02X} d1={d1:02X}"
            else:
                filt += f" d0={d0:02X}"
        return filt

    loop = asyncio.get_event_loop()

    async def ainput(prompt: str = "") -> str:
        return await loop.run_in_executor(None, lambda: input(prompt))

    try:
        while True:
            line = (await ainput("> ")).strip()
            if not line:
                continue
            parts = line.split()
            cmd, *rest = parts
            cmd = cmd.lower()

            if cmd in ("quit", "exit"):
                break

            elif cmd == "help":
                print(_cmd_repl.__doc__)

            elif cmd == "watch":
                if not global_watcher_registered:
                    client.on_frame(_global_watcher)
                watching = True
                global_watcher_registered = True
                print("Watching all frames.")

            elif cmd == "unwatch":
                if not rest:
                    # unwatch global 'watch'
                    if watching:
                        watching = False
                        print("Stopped global watch.")
                    else:
                        print("Global watch is not active.")
                    continue

                arg = rest[0].lower()
                if arg in ("watch", "global"):
                    watching = False
                    print("Stopped global watch.")
                    continue

                if arg == "all":
                    # stop global + disable all specific matchers
                    watching = False
                    # try to fully unregister if client supports it
                    for wid, (cid, d0, d1, cb, active) in list(watches.items()):
                        if active and hasattr(client, "unregister_callback"):
                            with suppress(Exception):
                                removed = client.unregister_callback(cid, d0, d1, cb)
                                # ignore 'removed' count; we mark inactive regardless
                        watches[wid] = (cid, d0, d1, cb, False)
                    print("Cleared all watchers.")
                    continue

                # numeric ID path
                try:
                    wid = int(rest[0], 10)
                except ValueError:
                    print("unwatch: argument must be 'watch', 'all', or a numeric watcher id")
                    continue

                if wid not in watches:
                    print(f"unwatch: no watcher with id {wid}")
                    continue

                cid, d0, d1, cb, active = watches[wid]
                if active and hasattr(client, "unregister_callback"):
                    try:
                        removed = client.unregister_callback(cid, d0, d1, cb)
                        if removed:
                            watches[wid] = (cid, d0, d1, cb, False)
                            print(f"unwatch: removed watcher #{wid}")
                            continue
                    except Exception as e:
                        # fall back to deactivating locally
                        print(f"unwatch: backend unregister failed ({e}); deactivating locally")

                # Local deactivate (works even if client lacks unregister)
                watches[wid] = (cid, d0, d1, cb, False)
                print(f"unwatch: deactivated watcher #{wid}")
                continue

            elif cmd == "list":
                any_rows = False
                print("Watchers:")
                print(f"  Global watch: {'ON' if watching else 'OFF'}")
                for wid, (cid, d0, d1, _cb, active) in sorted(watches.items()):
                    any_rows = True
                    state = "active" if active else "inactive"
                    print(f"  #{wid:<3} {_fmt_filter(cid, d0, d1):<24} [{state}]")
                if not any_rows:
                    print("  (none)")
                continue

            elif cmd == "send":
                if len(rest) < 2:
                    print("Usage: send <id> <hex> [ext|std] [rtr]")
                    continue
                try:
                    can_id = _parse_can_id(rest[0])
                except ValueError as e:
                    print(f"Error: {e}")
                    continue
                try:
                    data = parse_hex_bytes(rest[1])
                except Exception as e:
                    print(f"Error parsing data bytes: {e}")
                    continue
                ext = None
                rtr = False
                if len(rest) >= 3:
                    if rest[2].lower() in ("ext", "extended"):
                        ext = True
                    elif rest[2].lower() in ("std", "standard"):
                        ext = False
                if len(rest) >= 4:
                    rtr = rest[3].lower() == "rtr"
                await client.send(can_id, data, extended=ext, rtr=rtr)
                print("Sent.")

            elif cmd == "on":
                if len(rest) < 1 or len(rest) > 3:
                    print("Usage: on <id> [d0] [d1]     (bytes are hex, e.g., 0x12)")
                    continue
                try:
                    can_id = _parse_can_id(rest[0])
                except ValueError as e:
                    print(f"Error: {e}")
                    continue

                d0 = d1 = None
                if len(rest) >= 2:
                    try:
                        d0 = _parse_byte(rest[1], "d0")
                    except ValueError as e:
                        print(f"Error: {e}")
                        continue
                if len(rest) >= 3:
                    try:
                        d1 = _parse_byte(rest[2], "d1")
                    except ValueError as e:
                        print(f"Error: {e}")
                        continue

                wid = next_watch_id
                next_watch_id += 1

                # Each callback checks its 'active' flag so we can deactivate locally if needed
                def _cb(frame: CANFrame, _wid=wid) -> None:
                    tup = watches.get(_wid)
                    if not tup:
                        return
                    cid, bd0, bd1, _fn, active = tup
                    if not active:
                        return
                    # Guard: ensure the callback remains bound to the same filter
                    # (in practice, client matches ensure this anyway)
                    data = " ".join(f"{b:02X}" for b in frame.data)
                    tag = _fmt_filter(cid, bd0, bd1)
                    print(f"[match {tag}] id=0x{frame.can_id:X} dlc={frame.dlc} data={data}")

                # Register with client and remember
                client.register_callback(can_id, d0, d1, _cb)
                watches[wid] = (can_id, d0, d1, _cb, True)

                print(f"watch #{wid}: {_fmt_filter(can_id, d0, d1)}")
                continue

            elif cmd == "wait":
                if len(rest) < 1:
                    print("Usage: wait <id> [d0] [d1] [timeout]")
                    continue
                try:
                    can_id = _parse_can_id(rest[0])
                except ValueError as e:
                    print(f"Error: {e}")
                    continue
                d0 = None
                d1 = None
                if len(rest) >= 2:
                    try:
                        d0 = _parse_byte(rest[1], "d0")
                    except ValueError as e:
                        print(f"Error: {e}")
                        continue
                if len(rest) >= 3:
                    try:
                        d1 = _parse_byte(rest[2], "d1")
                    except ValueError as e:
                        print(f"Error: {e}")
                        continue
                to = float(rest[3]) if len(rest) >= 4 else None
                try:
                    frame = await client.wait_for(can_id, d0=d0, d1=d1, timeout=to)
                    print("[WAIT MATCH]", frame)
                except (asyncio.TimeoutError, TimeoutError):
                    print("[WAIT TIMEOUT]")

            else:
                print("Unknown command. Type 'help' for help.")
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `caneth` console script."""
    parser = argparse.ArgumentParser(description="Asyncio CAN client for Waveshare 2-CH-CAN-TO-ETH")
    parser.add_argument("--host", required=True, help="Device IP (e.g., 192.168.0.7)")
    parser.add_argument("--port", required=True, type=int, help="TCP port for CAN channel (e.g., 20001 for CAN1)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for initial connection")
    parser.add_argument("--log", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_watch = sub.add_parser("watch", help="Connect and print all received frames")
    p_watch.set_defaults(func=_cmd_watch)

    p_send = sub.add_parser("send", help="Send a single CAN frame and exit")
    p_send.add_argument("--id", required=True, help="CAN ID (e.g., 0x123 or 0x12345678)")
    p_send.add_argument("--data", default="", help="Data bytes, hex (e.g., '12 34 56' or '123456')")
    p_send.add_argument("--extended", action="store_true", help="Force extended (29-bit) ID")
    p_send.add_argument("--std", dest="extended", action="store_false", help="Force standard (11-bit) ID")
    p_send.add_argument("--rtr", action="store_true", help="Remote frame")
    p_send.set_defaults(func=_cmd_send)

    p_repl = sub.add_parser("repl", help="Interactive console: send/filters/watch/unwatch/list")
    p_repl.set_defaults(func=_cmd_repl)

    p_wait = sub.add_parser("wait", help="Wait for a specific frame and print it")
    p_wait.add_argument("--id", required=True, help="CAN ID to match (e.g., 0x123)")
    p_wait.add_argument("--d0", help="Optional first data byte (hex, e.g., 0x01)")
    p_wait.add_argument("--d1", help="Optional second data byte (hex, e.g., 0x02)")
    p_wait.add_argument("--wait-timeout", type=float, default=None, help="Timeout in seconds")
    p_wait.set_defaults(func=_cmd_wait)

    args = parser.parse_args(argv)
    try:
        asyncio.run(args.func(args))
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
