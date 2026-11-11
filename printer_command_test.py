"""Read printer metrics configuration (M333), without sending movement commands."""
import argparse
import sys

import gantry_controls as gc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="explicit serial device; otherwise use Prusa VID detection")
    args = parser.parse_args(argv)
    if args.port is not None and not args.port.strip():
        parser.error("--port must name a serial device")
    previous_port = gc.PORT_OVERRIDE
    if args.port is not None:
        gc.PORT_OVERRIDE = args.port
    try:
        print(gc.send_gcode("M333"))
        return 0
    except (gc.PrinterError, OSError) as exc:
        print(f"Printer diagnostic failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            gc.disconnect()
        finally:
            gc.PORT_OVERRIDE = previous_port


if __name__ == "__main__":
    raise SystemExit(main())

