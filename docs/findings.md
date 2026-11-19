# Findings: PulseGuard

Defects and surprises found while building and testing, with the evidence and what was done. Ids continue across lessons.

## F-01: Discharge fails with 500 when the clock has not moved forward

- Found: 2026-11-16, d240, by a test written for the edge before the fix.
- Steps: admit a patient, then discharge at the same instant, or after the server's wall clock is set back (for example by NTP).
- Result: 500. The database refused the bed stay with `bed_stay_order_ck` (`to_at > from_at`), so the nurse could not discharge the patient at all.
- Severity: High. Discharge is blocked until the clock passes the admission time; the patient stays on the ward list as monitored.
- Fix: discharge time is `max(now, start of the current bed stay)`, logged as a warning when clamped; migration `0002` allows a stay that ends when it starts (`to_at >= from_at`).
- Tests: `test_discharge_at_the_same_instant_as_admission`, `test_discharge_when_the_wall_clock_went_back`.
- Related: R-ORD-03 (clock skew). The same pattern (`assignment_order_ck`) exists on device assignments and is checked in d241.

## F-02: Unassigning a device fails with 500 when the clock has not moved forward

- Found: 2026-11-16, d241, by tests written from F-01: the same constraint pattern existed on device assignments.
- Steps: assign a sensor to a session, then unassign it at the same instant, or after the wall clock is set back.
- Result: 500 from `assignment_order_ck` (`unassigned_at > assigned_at`). The state by action matrix also hit it: its ASSIGNED then unassign cell runs without moving the clock.
- Severity: High. A sensor put on the wrong patient by mistake cannot be taken off at once, and a discharge with such a sensor would fail the same way.
- Fix: unassign time is `max(now, assigned_at)`; migration `0003` allows `unassigned_at = assigned_at`.
- Tests: `test_unassign_at_the_same_instant_as_assign`, `test_unassign_when_the_wall_clock_went_back`, and the matrix cell ASSIGNED, unassign.
- Lesson: when a defect comes from a pattern, search for the pattern. All other order checks in the schema are now `>=` or allow the equal case: `session_closed_fields_ck` already did.

## Note: a race test can pass by luck

- In d241 the session row lock that keeps one sensor of each kind per session was removed on purpose. The test with 10 parallel assignments still passed in 1 of 5 runs.
- The test now repeats the race on 5 sessions. Without the lock it failed 10 of 10 runs; with the lock it passed 5 of 5.

## F-03: Two requirements disagreed about readings of a closed session

- Found: 2026-11-18, d242, while writing the test for a discharge during a backend outage.
- REQ-SES-03 said a reading for a closed session is refused. REQ-CON-03 says no buffered reading may be lost. A monitor that buffered readings measured at 10:00 and delivers them after a discharge at 10:05 cannot meet both.
- Decision: readings measured before the discharge are kept in the closed session's history and never shown as current; readings measured after it are refused with `NO_SESSION`. REQ-SES-03 now says so.
- Test: `test_after_discharge_only_readings_measured_before_it_are_kept`.
- Kind: a requirements defect, found before any code depended on the old wording.

## Note: two layers make one mutant harmless (d243)

- The ingest step runs only newly stored readings through the alarm engine, so a resent batch cannot raise an alarm twice. Removing that filter on purpose did not fail any test.
- The reason: a duplicate is never newer than the last reading the engine judged, and the engine skips such readings by itself. The mutant is equivalent by design, as the open session check in d240 was: the filter saves work, the engine's own order check keeps it correct.
- The race lesson of d241 came back: without the alarm row lock, two nurses acknowledging at once still passed 1 run in 8. The test now has 10 nurses and 3 alarms in turn; without the lock it failed 10 of 10 runs, with it it passed 5 of 5.

## Note: two weak tests found by mutants (d244)

- A token signed with HS512 was refused by its test, but because it lacked claims, not because of its algorithm. A mutant that allowed HS512 survived. The test now signs a complete, valid set of claims with HS256 (accepted) and with HS512 (refused).
- No test deactivated a user without raising `token_version`. Since the API always raises it, a mutant that ignored the `active` flag survived. A deactivation made straight in the database, as in an emergency, would have left the token working. The test now does exactly that.
- Both are tests that passed for the wrong reason. A green test only proves what it checks; mutants show what it does not.
