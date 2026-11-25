"""Print what a simulated sensor would send, one JSON message per line.

python -m pulseguard.simulator --scenario tachycardia --seed 1 --seconds 90
"""

import argparse
import asyncio
import json
from datetime import datetime

from pulseguard.clock import SystemClock
from pulseguard.simulator import wire
from pulseguard.simulator.client import stream
from pulseguard.simulator.generator import SensorSimulator
from pulseguard.simulator.scenarios import SCENARIOS, scenario_named


async def send_to_monitor(args: argparse.Namespace) -> None:
    """Real time: one second of simulated time per second, starting now."""
    host, port = args.connect.rsplit(":", 1)
    clock = SystemClock()
    sim = SensorSimulator(
        args.device, args.device_type, scenario_named(args.scenario), args.seed, clock.now()
    )
    replies = await stream(host, int(port), sim, args.seconds, clock, pace_s=1.0)
    for reply in replies:
        print(json.dumps(reply, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(prog="pulseguard.simulator")
    parser.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    parser.add_argument("--device", default="SN-PO-0001")
    parser.add_argument(
        "--device-type", default="PULSE_OXIMETER", choices=["PULSE_OXIMETER", "RESPIRATION"]
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--connect", metavar="HOST:PORT", help="send to a monitor instead of printing"
    )
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument(
        "--start", default="2026-11-12T18:52:00+00:00", help="first moment, with an offset"
    )
    args = parser.parse_args()

    if args.connect:
        asyncio.run(send_to_monitor(args))
        return
    start = datetime.fromisoformat(args.start)
    sim = SensorSimulator(
        args.device, args.device_type, scenario_named(args.scenario), args.seed, start
    )
    print(
        json.dumps(
            wire.hello(sim.device, sim.device_type, sim.parameters, start), separators=(",", ":")
        )
    )
    for tick in sim.run(args.seconds):
        if not tick.signal:
            print(json.dumps(wire.heartbeat("NO_SIGNAL", tick.at), separators=(",", ":")))
        for reading in tick.readings:
            print(json.dumps(wire.measurement(sim.device, reading, tick.at), separators=(",", ":")))


if __name__ == "__main__":
    main()
