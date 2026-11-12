# Database: PulseGuard

The PostgreSQL schema and the rules it enforces by itself. The draft is `db/schema-draft.sql`; the Alembic migrations from d239 onward follow it and then become the source of truth.

Last updated: 2026-11-12.

## Tables

| Area | Tables | Requirements |
|---|---|---|
| Units and users | `unit`, `bed`, `app_user`, `user_unit` | REQ-ACC-01, REQ-ACC-02 |
| Sessions | `patient`, `monitoring_session`, `bed_stay` | REQ-SES-01 to REQ-SES-05 |
| Devices | `device`, `device_assignment`, `device_connection` | REQ-DEV-01 to REQ-DEV-05, REQ-CON-05 |
| Measurements | `measurement`, `reading_rejection` | REQ-MEA-01 to REQ-MEA-06 |
| Limits | `absolute_limit`, `session_limit`, `session_limit_change` | REQ-ALM-06 |
| Alarms | `alarm`, `alarm_event` | REQ-ALM-01 to REQ-ALM-08 |

## Rules the database enforces

These hold even when two requests race, because the database refuses the second write. Project 2 showed that a check in code followed by an insert lets parallel requests through (F-05 there).

| Rule | Constraint | Requirement | Risk |
|---|---|---|---|
| One open session per patient | `session_one_open_per_patient_uq`, partial unique index | REQ-SES-02 | R-ISO-02 |
| A bed holds one session at a time | `bed_stay_no_overlap_per_bed`, exclusion on time ranges | REQ-SES-01 | R-ISO-02 |
| A session is in one bed at a time | `bed_stay_no_overlap_per_session` | REQ-SES-04 | R-ISO-01 |
| A device is in one session at a time | `assignment_no_overlap_per_device` | REQ-DEV-02 | R-ISO-02 |
| One stored reading per device and seq | `measurement_device_seq_key` | REQ-MEA-03 | R-ORD-01, R-ORD-04 |
| Plausible ranges, SpO2 whole numbers | `measurement_plausible_ck` | REQ-MEA-02 | R-INPUT-01 |
| One unresolved alarm per condition | `alarm_one_open_per_condition_uq` | REQ-ALM-02 | R-ALM-04 |
| Alarm fields match its state | `alarm_state_fields_ck`, `alarm_ack_pair_ck` | REQ-ALM-03 | R-ALM-03 |
| Limits go strictly up | `session_limit_order_ck` | REQ-ALM-06 | R-ALM-06 |
| Monitors have a credential | `device_monitor_credential_ck` | REQ-ACC-03 | R-PROTO-02 |

Time ranges are half open, `[from, to)`: a stay or assignment that ends at 10:00 and the next one that starts at 10:00 do not overlap, so a move or a handover at the same instant is allowed.

## Rules left to code, and why

- One patient monitor and one sensor per parameter in a session: the device kind is in another table, so a constraint cannot see it. The assigning transaction locks the session row first (`SELECT ... FOR UPDATE`), then checks; the lock makes parallel assignments wait for each other.
- Absolute limits on session limits: they change at run time, so they are checked by the API when limits are set.
- Readings outside the time window (REQ-MEA-05): the window depends on the current time, which a constraint should not use.
- State transitions such as RESOLVED never going back to ACTIVE: checked by the alarm engine; `alarm_event` keeps the history so a test can see every step.

## Decisions

- **Assignments are history, not a column on the device.** A late reading is matched to the session its device had at the measured time, with `tstzrange(assigned_at, unassigned_at) @> measured_at` (REQ-MEA-06).
- **Session of a reading is stored on the reading.** Resolved once at ingest, so a later move or reassignment cannot change whose reading it was.
- **Alarm engine state after a restart is rebuilt, not stored.** Open alarms are rows in `alarm`; a condition still in its debounce time is rebuilt by replaying the last 60 s of measurements of each open session through the engine. This keeps one source of truth and is tested by restarting the backend in the middle of a debounce (R-REC-04).
- **Closed sessions are not deleted.** After 6 days they are hidden from lists by the API (REQ-SES-05); deleting synthetic data is not worth the risk of a cascade, which deleted the admin user in Project 2's demo database.
- **Values are `numeric(5,1)`.** Exact decimal, so a band edge such as 93 compares exactly, which a float might not.

## How it was checked

`db/schema-checks.sh` loads the draft into a fresh database and runs `db/schema-checks.sql`: 31 checks that a forbidden write is refused by the expected constraint, or that an allowed one is accepted, plus 10 parallel admissions of one patient.

Run on 2026-11-12 on PostgreSQL 16.15 (the project will use 17 in Docker; d239 runs the checks there):

```
PGHOST=/tmp PGPORT=5433 PGUSER=postgres docs/db/schema-checks.sh
```

- 32 of 32 PASS, including "10 parallel admissions: 1 open session".
- One expectation of mine was wrong in the first run: an ACKNOWLEDGED alarm without `acknowledged_by` is refused by `alarm_ack_pair_ck`, not by `alarm_state_fields_ck`. Both forbid it; the check now names the one that fires.
- To see that the checks can fail, 8 constraints were weakened one at a time. Every weakening was caught, but 3 only after checks were added: a reading with the same seq and other content (R-ORD-04), a second alarm while the first is ACKNOWLEDGED, and a move at the exact instant another session leaves the bed.

| Weakened constraint | Caught by |
|---|---|
| Open session index not unique | Second open session; 10 parallel admissions gave 10 open sessions |
| Assignment overlap compared with `=` instead of `&&` | Same device to session B; REQ-MEA-06 lookup |
| Duplicate key includes `measured_at` | Same seq with other content (added) |
| Open alarm index only on ACTIVE | Second alarm while one is ACKNOWLEDGED (added) |
| Bed range closed at both ends | B into B1 at exactly 10:00 (added) |
| Session range closed at both ends | A into B2 at exactly 10:00 (added) |
| Assignment range closed at both ends | Assign to B at exactly 10:00; REQ-MEA-06 lookup |
| Pulse upper bound 301 | Pulse rate 301 |
