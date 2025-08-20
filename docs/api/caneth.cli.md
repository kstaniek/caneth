# caneth.cli

Command Line Interface for caneth.

Commands:
  - watch: print all received frames
  - send:  send a single frame
  - wait:  wait for (and print) a matching frame
  - repl:  interactive shell (watch, send, on, wait, help, quit)

## Functions

### _cmd_repl `(args: 'argparse.Namespace') -> 'None'`

Minimal interactive console.

    Commands:
      send <id> <hex> [ext|std] [rtr]
      on <id> [d0] [d1]   # register a matcher (by ID, or ID+d0, or ID+d0+d1)
      watch               # print all frames
      wait <id> [d0] [d1] [timeout]
      help
      quit/exit

### _cmd_send `(args: 'argparse.Namespace') -> 'None'`

Send a single frame and exit.

### _cmd_wait `(args: 'argparse.Namespace') -> 'None'`

Wait for a specific frame (optionally d0/d1) and print it or timeout.

### _cmd_watch `(args: 'argparse.Namespace') -> 'None'`

Connect and print all received frames until Ctrl-C.

### _parse_byte `(s: 'str', label: 'str' = 'byte') -> 'int'`

Parse a single byte from decimal or hex (0..255). Accepts '255', '0xFF', 'ff'.

### _parse_can_id `(s: 'str') -> 'int'`

Parse CAN ID from decimal or hex. Accepts forms like: 291, 0x123, 123 (hex).

### main `(argv: 'Optional[list[str]]' = None) -> 'int'`

Entry point for the `caneth` console script.