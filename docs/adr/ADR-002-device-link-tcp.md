# ADR-002: TCP between sensor and patient monitor, HTTP to the backend

Status: Accepted. Date: 2026-11-12.

## Context

Sensors send a reading every second or every 5 s to the patient monitor of their bed (REQ-MEA-01). The link must survive drops and resends without losing or doubling readings (REQ-CON-02, REQ-CON-03), and refuse broken messages without dying (REQ-CON-01). The monitor then forwards readings to the backend, which may be down for minutes.

Real wearables use radio links such as Bluetooth Low Energy into a gateway; we cannot test radio here. The link we build should expose the same problems: framing, acknowledgement, resend, ordering, timeouts.

## Options for sensor to monitor

| Option | For | Against |
|---|---|---|
| Raw TCP with length-prefixed JSON messages | Close to a serial or radio device link; framing, ACK, sequence numbers and timeouts are our own, so each can be tested and broken (d258, d259); works with `tc netem` and `tcpdump` (d278) | We must write and test framing and resend ourselves |
| MQTT through a broker | Common in IoT; the broker handles delivery levels | Adds a broker to run; delivery and resend are inside the broker, so the risks we want to test are hidden; Project 1 already used a message based pipeline |
| WebSocket | Framing is given; easy from a browser | Framing and resend come from the library, little left to test; a browser is not part of this link |
| HTTP POST per reading | Simplest | One request per reading per second per sensor; no long lived connection, so no heartbeat or connection loss to model |
| Virtual serial port with `socat` | Closest to a wired device | Harder to run in CI; kept as an optional extra (d278) |

## Options for monitor to backend

| Option | For | Against |
|---|---|---|
| HTTP JSON batches | The backend already serves HTTP; a retried batch is safe because each reading is idempotent by device and sequence number; easy to test with httpx | Not pushed in real time; the monitor must buffer |
| The same TCP protocol all the way | One protocol | A second TCP server in the backend; harder to authenticate and to test than HTTP |

## Decision

- Sensor to monitor: TCP with length-prefixed JSON. Every message has a version, a type (HELLO, PAIR, MEASUREMENT, HEARTBEAT, ACK) and, for readings, a sequence number and a measured time. The full spec is `protocol.md` (d237).
- Monitor to backend: HTTP JSON batches, each reading keyed by device and sequence number; the response lists the accepted sequence numbers.

## Consequences

- Framing bugs are possible and must be tested: partial frames, frames stuck together in one read, frames over the size limit, broken JSON, unknown versions (R-PROTO-01).
- The sensor keeps unacknowledged readings and resends them; the monitor keeps a disk buffer. Duplicates are expected and removed by the backend (R-ORD-01, R-REC-01).
- Latency from sensor to dashboard includes the monitor's batch interval, which must stay well below the 5 s in REQ-NFR-01; the default is 1 s.

## How we will know

- Protocol tests send each kind of broken frame and check that the connection stays usable and valid frames after it are accepted.
- A simulator test cuts the link for 60 s and checks that every reading arrives exactly once.
