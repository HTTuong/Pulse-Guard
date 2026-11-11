# Product Requirements: PulseGuard

What PulseGuard must do, as requirements with ids and measurable acceptance criteria. Every later test names the requirement it checks, so a requirement without a test, or a test without a requirement, shows up in the traceability matrix.

Last updated: 2026-11-11. Based on `product-research.md`.

> PulseGuard is a simulated system for practising testing. It is not a medical device. Thresholds and clinical rules below are design assumptions, taken from public guidance where possible, not clinical advice.

## Conventions

- Ids are `REQ-<AREA>-<NN>`: SES sessions, DEV devices, MEA measurements, ALM alarms, CON connectivity, ACC access, VIEW central view, NFR non-functional.
- "Must" requirements are needed for the first release; "Should" ones may follow.
- Times are measured on the backend clock in UTC; displays use the unit's time zone (Europe/Helsinki by default).
- A sensor sends one reading per second for pulse rate and SpO2 and one every 5 s for respiration rate.

## Users and roles

| Role | Works on | Main tasks |
|---|---|---|
| Nurse | Patients of their own units | Admit and discharge, pair sensors, watch the central view, acknowledge alarms, adjust alarm limits within absolute limits |
| Physician | Patients of their own units | Watch the central view and history, acknowledge alarms, adjust alarm limits within absolute limits |
| Technician | Devices | Register, assign, unassign and retire devices; sees no vital signs |
| Admin | Units and users | Manage units, beds and users; sets the absolute limits |

## Decisions on the open questions

| Question | Decision | Why |
|---|---|---|
| Default alarm limits and priorities | High priority where a value scores 3 in NEWS2, medium where it scores 2, no alarm where it scores 1 or 0 | Reuses published early warning bands; alarming on score 1 (for example a pulse of 95) would cause alarm fatigue |
| May staff change limits | Nurses and physicians may change a patient's limits within absolute limits set by an admin; every change is recorded | The real products allow it within locked absolute limits |
| Debounce | A high priority condition must last 5 s, a medium one 10 s, before an alarm is raised | Short spikes, such as a sensor moving, do not alarm; dangerous values alarm fast |
| Latching | High priority alarms latch: they stay until acknowledged, even when the value returns; medium ones resolve by themselves after 10 s back in range | A dangerous event must be seen by a person; a borderline one should not need a click |
| When is a sensor lost | No reading from a sensor for 15 s raises a technical alarm; it resolves after 15 s of continuous data | Mirrors the 15 s reactivation in the vendor's telemetry example |
| Data after discharge | Kept read-only for 6 days, then no longer shown | Mirrors the central station's 6 days |

## Default alarm limits

From the NEWS2 bands ([NWAS](https://www.nwas.nhs.uk/publications/news2-national-early-warning-score), [RCN](https://preprodstartingout.rcn.org.uk/Develop/Nursing-Essentials-Crib-Cards/NEWS2)); SpO2 uses scale 1. The bands must be checked against the official RCP NEWS2 chart before the alarm tests rely on them, because the fetched source text was partly garbled.

| Parameter | Unit | High priority | Medium priority | No alarm | Absolute limits for any setting |
|---|---|---|---|---|---|
| Respiration rate | breaths/min | ≤ 8 or ≥ 25 | 21 to 24 | 9 to 20 | 4 to 60 |
| SpO2 | % | ≤ 91 | 92 to 93 | ≥ 94 | 80 to 100 |
| Pulse rate | beats/min | ≤ 40 or ≥ 131 | 111 to 130 | 41 to 110 | 30 to 200 |

## Sessions (admission and discharge)

**REQ-SES-01 (Must)** A nurse admits a patient to a free bed of their unit, which opens a monitoring session.
- The session is OPEN, names the patient, the unit and the bed, and records who admitted and when.
- Admission to a bed that has an open session, to an unknown bed or to a bed of another unit is refused with a reason.

**REQ-SES-02 (Must)** A patient has at most one open session at a time.
- A second admission of a patient with an open session is refused with 409, also when two admissions are sent at the same moment.

**REQ-SES-03 (Must)** A nurse discharges a patient, which closes the session.
- The session becomes CLOSED with who and when; all its devices are unassigned (REQ-DEV-03); its open alarms become RESOLVED with the reason "discharged".
- A reading for a closed session is refused and never shown as current.

**REQ-SES-04 (Must)** A patient moves to another bed or unit without losing the session's history.
- The session keeps its id, measurements and alarms; the move is recorded; staff of the new unit see it, staff of the old unit no longer do.

**REQ-SES-05 (Should)** A closed session's data stays readable for 6 days after discharge, read-only.
- Within 6 days it can be opened from the history; after 6 days it is not listed; nothing in it can change.

## Devices (lifecycle and pairing)

**REQ-DEV-01 (Must)** A technician registers a device (patient monitor or sensor) with a unique serial number and a type.
- A duplicate serial number is refused with 409. A new device is AVAILABLE.

**REQ-DEV-02 (Must)** A device is assigned to one open session at a time.
- Assigning an AVAILABLE device to an open session makes it ASSIGNED to that session.
- Assigning a device that is ASSIGNED or RETIRED, or to a closed session, is refused with a reason.
- A session has one patient monitor and at most one sensor per parameter.

**REQ-DEV-03 (Must)** A device is unassigned on request or when its session closes, and becomes AVAILABLE.
- After unassignment no reading from it is accepted for the old session.

**REQ-DEV-04 (Must)** A technician retires a device that is not assigned; a retired device never comes back.
- Retiring an ASSIGNED device is refused. A RETIRED device cannot be assigned and its readings are refused.

**REQ-DEV-05 (Must)** A sensor is paired with its session's patient monitor, and a reading is accepted only from a paired sensor.
- A sensor paired to another session's monitor, or not paired at all, has its readings refused and the refusal logged with the serial number.

## Measurements

**REQ-MEA-01 (Must)** A reading carries the device, a sequence number, the time it was measured, the parameter, the value and the unit.
- A reading without one of these, with an unknown parameter or with the wrong unit is refused.

**REQ-MEA-02 (Must)** A value outside the plausible range of its parameter is refused, not stored as a measurement, and counted per sensor.
- Plausible ranges: respiration rate 0 to 120, SpO2 0 to 100 (whole numbers), pulse rate 0 to 300.

**REQ-MEA-03 (Must)** A reading with the same device and sequence number as an earlier one is a duplicate: it is acknowledged again and stored once.
- Sending the same reading 3 times leaves exactly 1 stored measurement.

**REQ-MEA-04 (Must)** Readings that arrive out of order are stored in order of their measured time, and the current value is the reading with the latest measured time.
- Readings measured at t1 < t2 < t3 that arrive t3, t1, t2 give the history t1, t2, t3 and the current value of t3.

**REQ-MEA-05 (Must)** A reading measured more than 60 s in the future, or more than 24 h in the past, is refused.

**REQ-MEA-06 (Must)** A reading belongs to the session its sensor was assigned to at the measured time, never to the next patient.
- After a sensor moves from patient A to patient B, a late reading measured before the move is stored for A, or refused, and never shown for B.

## Alarms

**REQ-ALM-01 (Must)** A physiological alarm is raised when a parameter stays in its high or medium band for the debounce time: 5 s for high, 10 s for medium priority.
- A pulse of 140 for 6 s raises one high priority alarm; for 4 s it raises none.
- A pulse of 120 for 11 s raises one medium priority alarm; for 9 s it raises none.

**REQ-ALM-02 (Must)** A condition raises one alarm, not one per reading.
- 60 s of a pulse of 140 give exactly 1 alarm. If it gets worse, the existing alarm's priority rises from medium to high, still 1 alarm.

**REQ-ALM-03 (Must)** An alarm moves ACTIVE → ACKNOWLEDGED → RESOLVED; each change records who, when and why.
- Acknowledging requires a nurse or physician of the patient's unit; an acknowledged alarm stays visible until resolved.
- Every state change and its user are in the alarm's history.

**REQ-ALM-04 (Must)** High priority alarms latch; medium priority alarms do not.
- A high priority alarm whose value returns to normal stays ACTIVE, marked "condition cleared", until acknowledged; then it becomes RESOLVED.
- A medium priority alarm resolves by itself after its value has been back out of the alarm band for 10 s, marked "resolved without acknowledgement".

**REQ-ALM-05 (Must)** A technical alarm SENSOR_LOST (medium priority) is raised when a sensor of an open session sends nothing for 15 s, and MONITOR_LOST (high priority) when the patient monitor sends no heartbeat for 15 s.
- It resolves after 15 s of continuous data from that device.
- While a parameter has no data, no physiological alarm is raised or resolved for it.

**REQ-ALM-06 (Must)** A nurse or physician may set a session's limits within the absolute limits; the change applies to new readings at once and is recorded.
- A limit outside the absolute limits is refused. The record shows the old and new limit, who and when.

**REQ-ALM-07 (Should)** A high priority alarm not acknowledged within 120 s is escalated: it is marked escalated and notified to all staff of the unit on shift.

**REQ-ALM-08 (Must)** Alarms of a closed session are never shown as current (see REQ-SES-03).

## Connectivity

**REQ-CON-01 (Must)** Sensors, the patient monitor and the backend exchange versioned messages; a message of an unknown version, or one that cannot be read, is refused and logged, and the connection stays usable.
- The protocol itself is specified in `protocol.md` (d237).

**REQ-CON-02 (Must)** The patient monitor acknowledges each reading; a sensor resends a reading until it is acknowledged.
- After a broken connection, every reading not yet acknowledged is sent again once the connection is back, and arrives exactly once in storage (REQ-MEA-03).

**REQ-CON-03 (Must)** The patient monitor buffers readings while the backend cannot be reached and forwards them in measured order when it can.
- With the backend down for 10 minutes, no reading is lost and none is stored twice; the buffer holds at least 10 minutes of a full session (3 parameters).

**REQ-CON-04 (Must)** A device reconnects by itself after a lost connection, with growing waits between attempts.
- Attempts start after 1 s and double up to 30 s; the device never stops trying while assigned.

**REQ-CON-05 (Must)** The current status of each device is visible: CONNECTED, or LOST since a given time.

## Access

**REQ-ACC-01 (Must)** Every request needs a valid token except login; a token is valid for at most 8 hours.

**REQ-ACC-02 (Must)** Nurses and physicians see and act only on sessions of their own units; technicians see devices but no vital signs; admins manage units and users.
- A request outside one's units or role is refused with 403 and changes nothing.

**REQ-ACC-03 (Must)** Devices authenticate to the backend with their own credentials; a reading from an unknown or retired device is refused.

## Central view

**REQ-VIEW-01 (Must)** The central view lists the open sessions of the user's units with the latest value of each parameter and its age.
- A value older than 15 s is shown as stale, never as current.

**REQ-VIEW-02 (Must)** Alarms are listed by priority, then by start time, oldest first; a technical alarm is shown next to the parameter it affects.

**REQ-VIEW-03 (Should)** The history of a session shows each parameter over a chosen time range, downsampled for long ranges, with its alarms on the same time line.

## Non-functional

**REQ-NFR-01 (Must)** From the moment a sensor measures a value in a high priority band to the alarm appearing in the central view takes at most 5 s plus the debounce time, for 95 % of alarms with 50 open sessions.

**REQ-NFR-02 (Must)** The system handles 50 open sessions with 3 parameters each (about 110 readings per second) for 1 hour without losing a reading.

**REQ-NFR-03 (Must)** Simulated sensors produce the same readings for the same seed, so every test can be run again exactly.

**REQ-NFR-04 (Must)** No real personal data is used anywhere; names, ids and vital signs are generated.

## Coverage of the workflow

| Workflow step | Requirements |
|---|---|
| Admission | REQ-SES-01, REQ-SES-02 |
| Pairing | REQ-DEV-01, REQ-DEV-02, REQ-DEV-05 |
| Monitoring | REQ-MEA-01 to REQ-MEA-06, REQ-CON-01 to REQ-CON-05, REQ-VIEW-01 |
| Alarm | REQ-ALM-01 to REQ-ALM-08, REQ-VIEW-02 |
| Discharge | REQ-SES-03, REQ-SES-05, REQ-DEV-03 |
| Reassignment | REQ-SES-04, REQ-DEV-03, REQ-DEV-04, REQ-MEA-06 |
| Across all steps | REQ-ACC-01 to REQ-ACC-03, REQ-NFR-01 to REQ-NFR-04, REQ-VIEW-03 |

39 requirements: 36 Must, 3 Should.
