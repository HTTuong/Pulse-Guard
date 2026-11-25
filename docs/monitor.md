# Patient monitor (TCP server)

The monitor is the TCP server that sensors connect to. It applies the rules of `protocol.md` to every frame, acknowledges readings it has kept, and answers every frame it refuses with an ERROR.

Run it by hand, with the simulator as a sensor:

    python -m pulseguard.monitor --port 7600 --pair SN-PO-0001=pr-1
    python -m pulseguard.simulator --connect 127.0.0.1:7600 --scenario tachycardia --seconds 20

The monitor writes one JSON object per line to stderr; the simulator prints every frame the monitor sent back.

## Layers

| Layer | File | Does I/O | What it holds |
|---|---|---|---|
| Frames | `protocol/framing.py` | no | Length prefix, split and joined frames, bad lengths (P-01 to P-03) |
| JSON | `protocol/codec.py` | no | Strict JSON and strict times (P-04, P-05) |
| Rules | `monitor/session.py` | no | HELLO, pairing, MEASUREMENT, duplicates, every ERROR code |
| Buffer | `monitor/buffer.py` | no | Readings kept until the backend has them; duplicate and conflict detection |
| Server | `monitor/server.py` | yes | One task per connection, timers, heartbeats, shutdown |

The split is why most rules are tested with a function call and plain bytes, and only the timers and the connection life cycle need a socket.

## What each timer does

All values are in `MonitorConfig`; the tests use tenths of seconds.

| Timer | Default | Closes with reason |
|---|---|---|
| No HELLO after connecting | 5 s | `hello_timeout` |
| A frame that has started and is not complete | 5 s | `incomplete_frame` |
| No complete frame at all | 15 s | `silent` (the monitor's own heartbeats do not count) |
| Bad frame length | at once | `bad_length`, after ERROR BAD_LENGTH |
| Version not supported in HELLO | at once | `unsupported_version` |

The nearest deadline decides how long a read may wait, so one loop serves all three timers. They use the event loop's clock, which is monotonic (P-18): a test moves the wall clock by years and no timer fires.

## Decisions made while writing it

- ACK follows `buffer.store`. The buffer is in memory, so an ACK does not yet survive a restart; REQ-CON-03 is met when the disk buffer comes with d250. If `store` raises, nothing is acknowledged and the connection is closed (reason `internal_error`), and other connections go on.
- A first NOT_PAIRED is sent once, not at each 5 s check (F-04).
- ERROR is never answered with ERROR, so two monitors or sensors cannot ping-pong.
- A conflicting `seq` is refused but the connection stays open; the sensor decides what to do (R-ORD-04).
- Log fields that come from a sensor are cut at 200 characters and only ever appear inside a JSON string, so a device name with a newline cannot fake a log line.

## Log events

`listening`, `connection_open`, `hello`, `paired`, `not_paired`, `measurement` (with `result` STORED or DUPLICATE), `seq_gap`, `seq_conflict`, `frame_refused` (with `code`), `sensor_error`, `timeout` (with `timer`), `connection_error`, `internal_error`, `connection_closed` (with `reason`).

## Not here yet

- Unpairing while connected (P-10, PAIR with UNPAIRED): d249.
- Sensor side resend and reconnect with backoff: d249.
- Disk buffer, forwarding to the backend, SENSOR_LOST and MONITOR_LOST: d250.
- Two connections for the same serial are both accepted. What the monitor should do is not in the protocol yet.
