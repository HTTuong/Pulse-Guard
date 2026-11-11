# Risk Analysis: PulseGuard

Quality risks of PulseGuard and how each one maps to requirements and planned tests: **Risk** → **Requirement** → **Test group**. It is written before any code exists, so the tests are planned from risks rather than from features. Each entry will be updated with evidence from real test runs.

Last updated: 2026-11-12. Based on `product-research.md` and `prd.md`.

> PulseGuard is a simulated system for practising testing. It is not a medical device. "Patient safety" below means the harm the same failure would cause in a real product; it is the yardstick for ordering the tests.

## How risks are rated

Severity is the worst plausible harm to a patient if the failure reaches the ward.

| Severity | Meaning | Example |
|---|---|---|
| Critical | A deteriorating patient can go unnoticed, or staff act on another patient's data | A high priority alarm is never raised; patient B's pulse is shown for patient A |
| High | A real event is noticed late, or staff lose trust in what they see | An alarm appears 30 s late; a stale value is shown as current |
| Medium | Extra work or noise, no direct harm | Duplicate alarms; a refused reading without a clear reason |

Likelihood is an estimate made before the code exists: High where the behavior depends on timing, concurrency or network faults, Medium where it depends on state changes, Low for plain validation. Priority is severity first, then likelihood; tests for P1 risks are written first and run on every commit.

## Planned test groups

| Group | What | Tool |
|---|---|---|
| U | Unit tests of pure logic: alarm state machine, bands, debounce, validation, ordering | pytest |
| PB | Property tests with fixed seeds: random reading streams against an independent alarm oracle | pytest with Hypothesis |
| PR | Protocol tests against the TCP server: framing, versions, malformed and partial messages, ACK | pytest with asyncio |
| API | API integration tests on a real PostgreSQL: sessions, devices, alarms, access | pytest with httpx |
| SIM | Scenario tests driving the sensor simulator with fault injection: delay, drop, duplicate, reorder, disconnect, clock skew | pytest and the simulator |
| CONC | Parallel requests on the same patient, bed or device | pytest with asyncio |
| E2E | Central view as staff see it: values, staleness, alarm list, acknowledge | Robot Framework |
| LOAD | 50 sessions for 1 hour: latency and lost readings | Load script with the simulator |

## Risk register

| Id | Risk | Severity | Likelihood | Priority | Requirements | Test groups |
|---|---|---|---|---|---|---|
| R-ISO-01 | A reading is stored for, or shown on, the wrong patient, for example a sensor moved from patient A to B sends a late reading of A | Critical | High | P1 | REQ-MEA-06, REQ-DEV-03, REQ-DEV-05, REQ-SES-04 | U, SIM, API, E2E |
| R-ISO-02 | One device is assigned to two sessions, or a patient has two open sessions, so data splits or mixes | Critical | Medium | P1 | REQ-SES-02, REQ-DEV-02 | API, CONC |
| R-ISO-03 | Staff see or act on patients of another unit, or a technician sees vital signs | High | Medium | P2 | REQ-ACC-02, REQ-SES-04 | API (role by endpoint matrix), E2E |
| R-STALE-01 | An old value is shown as current after a sensor stops, so a patient looks stable when nothing is measured | Critical | High | P1 | REQ-VIEW-01, REQ-CON-05, REQ-ALM-05 | U, SIM, E2E |
| R-STALE-02 | A closed session's values or alarms are still shown as current | High | Medium | P2 | REQ-SES-03, REQ-ALM-08 | API, E2E |
| R-ORD-01 | Duplicate readings after a resend are stored twice, inflate the history or raise a second alarm | Medium | High | P2 | REQ-MEA-03, REQ-CON-02, REQ-ALM-02 | U, PR, SIM |
| R-ORD-02 | Readings arriving out of order make an old value the current one, or make the alarm engine raise or resolve on the wrong data | High | High | P1 | REQ-MEA-04, REQ-ALM-01, REQ-ALM-04 | U, PB, SIM |
| R-ORD-03 | Device clock skew puts readings in the future or far past, so ordering and staleness are wrong | High | Medium | P2 | REQ-MEA-05, REQ-VIEW-01 | U, SIM |
| R-ALM-01 | A high priority condition does not raise an alarm, or raises it after more than the debounce time | Critical | Medium | P1 | REQ-ALM-01, REQ-NFR-01 | U (boundaries at 4, 5, 6 s), PB, SIM, LOAD |
| R-ALM-02 | A latched alarm clears by itself when the value returns, so a short dangerous event is never seen | Critical | Medium | P1 | REQ-ALM-04 | U, PB, E2E |
| R-ALM-03 | The alarm lifecycle accepts a wrong transition, for example a resolved alarm becomes active again, or acknowledge by someone outside the unit | High | Medium | P2 | REQ-ALM-03, REQ-ACC-02 | U (state by action matrix), API |
| R-ALM-04 | One condition raises many alarms, or a short spike raises one, so staff learn to ignore alarms | Medium | High | P2 | REQ-ALM-01, REQ-ALM-02 | U, PB, SIM |
| R-ALM-05 | Band edges are off by one, for example SpO2 92 treated as high instead of medium | High | Medium | P2 | REQ-ALM-01, default limits table | U (every band edge on both sides) |
| R-ALM-06 | A limit outside the absolute limits is accepted, or a limit change does not apply, so a patient is monitored against the wrong range | Critical | Low | P2 | REQ-ALM-06 | U, API |
| R-ALM-07 | A missing parameter is treated as normal and resolves an alarm, or a gap raises a false physiological alarm | High | Medium | P2 | REQ-ALM-05 | U, SIM |
| R-REC-01 | Readings are lost while the backend or the network is down, or are not forwarded after recovery | Critical | High | P1 | REQ-CON-02, REQ-CON-03 | SIM, PR |
| R-REC-02 | A device stops trying to reconnect, or reconnects in a tight loop that overloads the backend | High | Medium | P2 | REQ-CON-04 | SIM |
| R-REC-03 | A lost sensor or monitor does not raise a technical alarm, or the alarm does not resolve after recovery | Critical | Medium | P1 | REQ-ALM-05, REQ-CON-05 | U, SIM, E2E |
| R-REC-04 | After a backend restart, alarm state or debounce timers are lost, so an active alarm disappears or a condition starts counting again | High | Medium | P2 | REQ-ALM-01, REQ-ALM-04 | SIM, API |
| R-REC-05 | While the backend is unreachable no alarm is raised, and the dashboard keeps showing the last values as if the patient were monitored | Critical | Medium | P1 | REQ-VIEW-04, REQ-CON-03, REQ-NFR-01 | SIM, E2E. Accepted limit of the design (`architecture.md`): alarms are raised only in the backend |
| R-PROTO-01 | A malformed, partial or unknown version message crashes the connection or the server | High | High | P1 | REQ-CON-01 | PR |
| R-PROTO-02 | A reading from an unpaired, unknown or retired device is accepted | High | Medium | P2 | REQ-DEV-04, REQ-DEV-05, REQ-ACC-03 | PR, API |
| R-INPUT-01 | An impossible value, wrong unit or missing field is stored as a measurement | Medium | Low | P3 | REQ-MEA-01, REQ-MEA-02 | U, PR |
| R-PERF-01 | Under 50 sessions the alarm latency grows or readings are dropped | High | Medium | P2 | REQ-NFR-01, REQ-NFR-02 | LOAD |
| R-TEST-01 | Timing based tests are flaky, so real failures are dismissed as noise | Medium | High | P2 | REQ-NFR-03 | All groups: an injectable clock and seeded simulator instead of real waiting |
| R-DATA-01 | Real personal data ends up in fixtures, logs or screenshots | Medium | Low | P3 | REQ-NFR-04 | Factories with generated names; review of logs |

## Order of work

- P1 first, in this order: R-ISO-01, R-STALE-01, R-ALM-01, R-ALM-02, R-REC-01, R-REC-03, R-REC-05, R-ORD-02, R-ISO-02, R-PROTO-01.
- The alarm engine is pure logic fed by a clock it is given, so R-ALM-01, R-ALM-02, R-ORD-02 and R-TEST-01 can be tested without a network or real waiting.
- An independent alarm oracle, written separately from the engine, checks the property tests, as `utils/hard-rules.ts` did in Project 2.

## Requirements not yet covered by a risk

- REQ-SES-01, REQ-SES-05, REQ-DEV-01, REQ-ALM-07, REQ-VIEW-02, REQ-VIEW-03 and REQ-ACC-01 have no risk of their own above; they are still tested from the requirements, at lower priority.
