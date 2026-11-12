# Device Protocol: sensor to patient monitor

The protocol between a sensor and the patient monitor of its bed, version 1. It is written so that each rule can be checked by a protocol test (d258, d259); rule ids `P-xx` are what those tests name, next to the requirement.

Last updated: 2026-11-12. Decided in ADR-002. The link from the patient monitor to the backend is HTTP and is specified in `api.md` (d238).

> PulseGuard is a simulated system for practising testing. It is not a medical device.

## Roles and connection

- The patient monitor is the TCP server, port 7600 by default; each sensor is a client with one connection.
- A sensor opens the connection, sends HELLO, waits to be paired, then streams readings.
- Either side may close the connection; the sensor then reconnects (see Reconnection).

## Framing

**P-01** Every message is one frame: a 4 byte unsigned big-endian length N, followed by exactly N bytes of UTF-8 JSON.

**P-02** N must be from 1 to 4096. A frame with N = 0 or N > 4096 gets ERROR `BAD_LENGTH`, and the receiver closes the connection, because it can no longer trust where the next frame starts.

**P-03** A receiver must handle a frame split over several reads and several frames in one read. A frame that has started but is not complete within 5 s closes the connection.

**P-04** The JSON must be a single object. A frame that is not valid UTF-8, not valid JSON, not an object, contains `NaN` or `Infinity`, or repeats a key gets ERROR `BAD_JSON`; the connection stays open.
- Python's `json.loads` accepts `NaN` and keeps the last of repeated keys without an error (checked on 2026-11-12), so the decoder must reject both on purpose.

Example: a MEASUREMENT of 184 bytes is sent as the length `00 00 00 b8` followed by

```json
{"v":1,"type":"MEASUREMENT","device":"SN-PO-0001","seq":41,"parameter":"PR","value":72,"unit":"beats/min","measured_at":"2026-11-12T18:52:03.000Z","sent_at":"2026-11-12T18:52:03.020Z"}
```

## Common fields

| Field | Type | Rule |
|---|---|---|
| `v` | integer | Protocol version; must be the version agreed in HELLO (P-07) |
| `type` | string | One of HELLO, PAIR, MEASUREMENT, HEARTBEAT, ACK, ERROR |
| `sent_at` | string | Time the frame was sent, by the sender's clock |

**P-05** Times are ISO 8601 in UTC with milliseconds and a `Z`, for example `2026-11-12T18:52:03.000Z`. A time with another offset, without milliseconds or that does not exist gets ERROR `BAD_TIME`. Project 2 showed that accepting times without an offset leads to silent shifts (R-TIME-01 there).

**P-06** A missing required field gets ERROR `MISSING_FIELD` with the field's name; an unknown `type` gets `UNKNOWN_TYPE`; fields that are not listed here are ignored, so a newer sender can add fields without breaking an older receiver.

## Versions

**P-07** HELLO lists the versions the sensor supports in `versions`. The monitor answers with the highest version both support, in the ACK of HELLO; every later frame must carry that `v`.
- No common version: ERROR `UNSUPPORTED_VERSION`, then close.
- A later frame with another `v`: ERROR `UNSUPPORTED_VERSION` for that frame only; the connection stays open (REQ-CON-01).

## Messages

### HELLO (sensor to monitor)

```json
{"v":1,"type":"HELLO","device":"SN-PO-0001","device_type":"PULSE_OXIMETER","parameters":["PR","SPO2"],"versions":[1],"sent_at":"2026-11-12T18:52:00.000Z"}
```

| Field | Rule |
|---|---|
| `device` | Serial number of the sensor |
| `device_type` | `PULSE_OXIMETER` (PR and SPO2) or `RESPIRATION` (RR) |
| `parameters` | Parameters this sensor will send; must match its type |

**P-08** HELLO must be the first frame and must arrive within 5 s of connecting; otherwise the monitor closes. Any other frame before HELLO gets ERROR `HELLO_FIRST`. The monitor answers a valid HELLO with ACK (`"ack":"HELLO"`, `"version":1`, `"monitor":"<serial>"`).

### PAIR (monitor to sensor)

```json
{"v":1,"type":"PAIR","status":"PAIRED","pairing":"pr-7f3a","sent_at":"2026-11-12T18:52:00.050Z"}
```

**P-09** After HELLO the monitor sends PAIR with `status` PAIRED if the backend has paired this sensor with this monitor's session, or ERROR `NOT_PAIRED` if not, and keeps the connection open, checking again every 5 s. The sensor sends no MEASUREMENT before PAIRED. The pairing comes from the backend (REQ-DEV-05); the sensor never learns who the patient is.

**P-10** When the backend unassigns the sensor, the monitor sends PAIR with `status` UNPAIRED and the time `unpaired_at`. The sensor stops measuring, sends any readings it still has that were measured before `unpaired_at`, and the monitor refuses readings measured after it with ERROR `NOT_PAIRED` (REQ-MEA-06, R-ISO-01). The sensor answers each PAIR with ACK (`"ack":"PAIR"`).

### MEASUREMENT (sensor to monitor)

| Field | Rule |
|---|---|
| `device` | Must equal the `device` of HELLO, otherwise ERROR `WRONG_DEVICE` |
| `seq` | Sequence number, see P-14 |
| `parameter` | `PR`, `SPO2` or `RR`, and one of the parameters in HELLO |
| `value` | Number; SPO2 must be a whole number |
| `unit` | `beats/min` for PR, `%` for SPO2, `breaths/min` for RR; otherwise ERROR `BAD_UNIT` |
| `measured_at` | When the value was measured, by the sensor's clock |

**P-11** The monitor checks the plausible range (REQ-MEA-02: RR 0 to 120, SPO2 0 to 100, PR 0 to 300) and the time window (REQ-MEA-05: at most 60 s ahead of and 24 h behind the monitor's clock). It refuses with ERROR `OUT_OF_RANGE` or `BAD_TIME`. The backend checks the same rules again.

### ACK (both directions)

```json
{"v":1,"type":"ACK","ack":"MEASUREMENT","seq":41,"status":"STORED","sent_at":"2026-11-12T18:52:03.031Z"}
```

**P-12** The monitor sends ACK for a MEASUREMENT only after the reading is in its disk buffer, so an acknowledged reading survives a monitor restart (REQ-CON-03). `status` is STORED for a new reading and DUPLICATE for one it already has (P-14).

### HEARTBEAT (both directions)

```json
{"v":1,"type":"HEARTBEAT","status":"OK","sent_at":"2026-11-12T18:52:05.000Z"}
```

**P-13** Each side sends HEARTBEAT every 5 s, whether or not other frames are sent. A sensor that is connected but cannot measure, for example a probe off the finger, sends `status` NO_SIGNAL.

### ERROR (both directions)

```json
{"v":1,"type":"ERROR","code":"OUT_OF_RANGE","seq":42,"detail":"PR 412 is outside 0 to 300","sent_at":"2026-11-12T18:52:04.010Z"}
```

`seq` is present when the error is about one MEASUREMENT. Codes: `BAD_LENGTH`, `BAD_JSON`, `BAD_TIME`, `BAD_UNIT`, `MISSING_FIELD`, `UNKNOWN_TYPE`, `UNSUPPORTED_VERSION`, `HELLO_FIRST`, `NOT_PAIRED`, `WRONG_DEVICE`, `OUT_OF_RANGE`, `SEQ_CONFLICT`.

## Sequence numbers, duplicates and order

**P-14** `seq` starts at 1 for a new device and goes up by 1 for each MEASUREMENT, across all parameters of the device. It is never reused: not after a reconnect, not after the sensor restarts, so the sensor stores its last `seq`.
- Same `device` and `seq` with the same `parameter`, `value` and `measured_at`: a duplicate. ACK with status DUPLICATE, stored once (REQ-MEA-03).
- Same `device` and `seq` with different content: ERROR `SEQ_CONFLICT`, refused, logged and counted. This catches a sensor that restarted its numbering, whose new readings would otherwise be thrown away as duplicates (R-ORD-04).
- Readings may arrive in any order; the monitor forwards them all and the backend orders them by `measured_at` (REQ-MEA-04).
- A gap in `seq` is logged and counted, not refused; it means readings were lost before the monitor.

## Acknowledgement and resend

**P-15** A sensor keeps every MEASUREMENT until it gets ACK or ERROR for that `seq`. Both end the resend: ERROR means the reading is refused for good.

**P-16** A reading with no answer within 3 s is sent again with the same `seq` and content. At most 50 readings may be waiting for an answer at once; the rest queue in `seq` order.

**P-17** While not connected, the sensor keeps up to 1200 readings, 10 minutes of a pulse oximeter. When full, it drops the oldest and counts the drop; the gap shows on the monitor (P-14).

## Timeouts

| Timer | Value | When it fires |
|---|---|---|
| HELLO wait | 5 s after connect | Monitor closes (P-08) |
| Incomplete frame | 5 s after its first byte | Receiver closes (P-03) |
| ACK wait | 3 s per MEASUREMENT | Sensor resends (P-16) |
| Heartbeat interval | 5 s | Each side sends HEARTBEAT (P-13) |
| Silent connection | 15 s without any frame | Either side closes and the sensor reconnects |
| No reading | 15 s without a MEASUREMENT from a paired sensor | Monitor reports SENSOR_LOST to the backend; a HEARTBEAT does not reset this timer (REQ-ALM-05) |

**P-18** All timers use a monotonic clock, so a change of the wall clock does not fire or stop them. Times in messages use the wall clock in UTC.

## Reconnection

**P-19** After a closed or failed connection the sensor waits and tries again: 1 s, 2 s, 4 s, 8 s, 16 s, then 30 s for every next attempt, each plus or minus 10 % of random jitter from a seeded generator, so a test can predict it (REQ-CON-04, REQ-NFR-03). The sensor never stops trying while it has readings or is paired.

**P-20** After reconnecting, the sensor sends HELLO, waits for PAIR, then resends every reading still waiting for an answer, oldest `seq` first, before new ones.

## Requirements and risks covered

| Rule | Requirements | Risks |
|---|---|---|
| P-01 to P-04, P-06, P-07 | REQ-CON-01 | R-PROTO-01, R-INPUT-01 |
| P-05, P-11, P-18 | REQ-MEA-01, REQ-MEA-02, REQ-MEA-05 | R-ORD-03, R-INPUT-01 |
| P-08 to P-10 | REQ-DEV-05, REQ-MEA-06, REQ-ACC-03 | R-ISO-01, R-PROTO-02 |
| P-14 | REQ-MEA-03, REQ-MEA-04 | R-ORD-01, R-ORD-02, R-ORD-04 |
| P-12, P-15 to P-17, P-20 | REQ-CON-02, REQ-CON-03 | R-REC-01 |
| P-13, timeouts | REQ-ALM-05, REQ-CON-05 | R-STALE-01, R-REC-03 |
| P-19 | REQ-CON-04, REQ-NFR-03 | R-REC-02, R-TEST-01 |

## Open points

- Sensors are identified by serial number only; device credentials on this link (REQ-ACC-03) are left to the monitor to backend link for now. A sensor that sends another sensor's serial would be accepted if that serial is paired. Recorded as R-PROTO-03 in `risk-analysis.md`.
- No encryption on this link; it stands for a short local link inside the room.
