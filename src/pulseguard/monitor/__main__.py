"""Run a patient monitor by hand, with a fixed pairing table.

python -m pulseguard.monitor --port 7600 --pair SN-PO-0001=pr-1
python -m pulseguard.simulator --connect 127.0.0.1:7600 --scenario tachycardia
"""

import argparse
import asyncio
import contextlib

from pulseguard.clock import SystemClock
from pulseguard.monitor.logs import configure_logging
from pulseguard.monitor.pairing import StaticPairing
from pulseguard.monitor.server import MonitorConfig, MonitorServer


async def serve(host: str, port: int, serial: str, pairs: dict[str, str]) -> None:
    server = MonitorServer(SystemClock(), StaticPairing(pairs), config=MonitorConfig(serial=serial))
    await server.start(host, port)
    try:
        await asyncio.Event().wait()  # until Ctrl+C cancels it
    finally:
        await server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="pulseguard.monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7600)
    parser.add_argument("--serial", default="MON-0001")
    parser.add_argument("--pair", action="append", default=[], metavar="SERIAL=PAIRING_ID")
    args = parser.parse_args()
    pairs = dict(item.split("=", 1) for item in args.pair)
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(args.host, args.port, args.serial, pairs))


if __name__ == "__main__":
    main()
