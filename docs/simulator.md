# Sensor simulator

A simulated wearable sensor that produces the readings and messages of `protocol.md` v1. It stands in for real sensors in every test from the monitor (d248) onward.

Run it by hand:

    python -m pulseguard.simulator --scenario tachycardia --seed 1 --seconds 90

It prints one JSON message per line: a HELLO, then a MEASUREMENT per parameter per second, and a HEARTBEAT with status NO_SIGNAL for each second the probe is off.

## Repeatable by design (REQ-NFR-03)

- The same seed, device and scenario give the same readings. Checked in three processes with different `PYTHONHASHSEED` values: the output has the same md5.
- Each parameter has its own random generator, seeded from `seed:device:parameter`. A string is used because `hash()` of a string changes from one process to the next.
- Time is an argument. The simulator never reads the system clock.

## Scenarios

| Name | What happens | Alarm it should lead to |
|---|---|---|
| `normal` | PR 72, SPO2 97, RR 16 with a little noise | none |
| `tachycardia` | PR is normal for 30 s, rises to 140 by 60 s and stays there | PR_HIGH_HIGH (limit 131) |
| `desaturation` | SPO2 is normal for 30 s, falls to 88 by 60 s and stays there | SPO2_HIGH_LOW (limit 91) |
| `sensor_off` | no signal from 30 s to 60 s, normal before and after | SENSOR_LOST (after 15 s, d250) |

A scenario is data (keyframes and no-signal intervals), so a new one is a new entry.

## Rules it follows

- `seq` starts at 1 and goes up by 1 for each reading across parameters (P-14). A probe that is off takes no `seq`, because nothing was measured; so the monitor sees no gap, only silence.
- Values are whole numbers inside the plausible range (REQ-MEA-02). Every reading is also checked against the backend's own functions in `measurements/rules.py`.
- A PULSE_OXIMETER sends PR and SPO2; a RESPIRATION sensor sends RR only.

## Not here yet

- Sending over TCP, resending without ACK and reconnecting: d248 and d249.
- Fault injection (delay, drop, duplicate, reorder, disconnect, clock skew): added when the protocol tests need it (d258, d259).

## The sensor client (d249)

`SensorClient` (`simulator/sensor.py`) is a sensor that behaves as protocol.md asks when things go wrong. Run it by hand, and stop the monitor in the middle with `kill` (not Ctrl+C, which a background job ignores):

    python -m pulseguard.monitor --port 7600 --pair SN-PO-0001=pr-1
    python -m pulseguard.simulator --connect 127.0.0.1:7600 --scenario normal --seconds 30

| Rule | Where | What it does |
|---|---|---|
| P-15, P-16 | `outbox.py` | Keeps every reading until ACK or ERROR; sends again after 3 s; at most 50 waiting |
| P-17 | `outbox.py` | Holds at most 1200 readings and drops the oldest, counting the drops |
| P-19 | `backoff.py` | 1, 2, 4, 8, 16, then 30 s, each plus or minus 10 % from a seeded generator |
| P-20 | `outbox.py`, `sensor.py` | After a lost connection every unanswered reading goes again, oldest first, before new ones |
| P-09, P-10 | `sensor.py` | Sends nothing before PAIRED; on UNPAIRED stops measuring and drops readings measured since |
| P-13 | `sensor.py` | Heartbeat every 5 s, with NO_SIGNAL while the probe is off |

The outbox and the backoff do no I/O and take `now` as an argument, so their rules are tested with plain calls; only the connection life cycle needs sockets. The sensor tests put a proxy between the sensor and the monitor (`tests/sensor/faultproxy.py`) that can cut every connection, or hold one direction for a while.
