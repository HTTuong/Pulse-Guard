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

## Note: two tests that checked less than they said (d245)

- A test claimed that rounding bucket widths down would give one bucket too many. Widths are in microseconds, so rounding down makes a bucket less than a microsecond shorter, and its 120 readings over 120 s never reached the extra bucket. A mutant that rounded down survived. The test now fills a 2 s range with 2000 readings one millisecond apart, where the extra bucket does appear.
- Seeded random alarm scenarios never produced a condition that came back after it had cleared. The step is covered by a written scenario instead; measuring which steps the random runs reach is how this showed up.
- One expected time was wrong in a first draft (an alarm resolves at 21 s, not 31 s); the engine's own unit test settled it.

## F-04: The protocol had no error code for a field with a value that is not allowed

- Found: 2026-11-25, d248, while writing the checks of HELLO and MEASUREMENT for the monitor.
- `protocol.md` had `MISSING_FIELD` for an absent field, and `BAD_UNIT`, `BAD_TIME` and `OUT_OF_RANGE` for three particular fields. Nothing covered a `device_type` that does not exist, parameters that do not fit the device type, `seq` of 0 or a text, a `value` that is not a number, or a second HELLO. Each would have had to borrow a code that says something else.
- Decision: a new code `BAD_FIELD` (P-06). The protocol is version 1 of a simulated system that nothing has used yet, so adding a code is not a version change.
- Also written down in `protocol.md` because the code needed a decision: ERROR `NOT_PAIRED` is sent once, not every 5 s (P-09); a `PAIR` frame from a sensor is `UNKNOWN_TYPE`.
- Kind: a specification gap, found by writing the code that has to apply the specification.

## F-05: `"v": true` was accepted as protocol version 1

- Found: 2026-11-25, d248, by a test of wrong version values written before the check was strict.
- Cause: in Python `True == 1` and `1.0 == 1`, so `message["v"] != version` let both through. A sensor that sent `"v": true` was treated as speaking version 1.
- Fix: the version must be the integer itself (`type(v) is int`), and P-07 says so. The test lists `2, 0, "1", null, 1.5, 1.0, true, [1]`.
- Lesson: the same trap exists for any `==` on a decoded JSON value. `seq` and `value` already reject `bool`; `parse_wire_time` accepts only text.

## Note: a limit I assumed was wrong, measured instead (d248)

- I expected a frame of 4096 bytes could nest JSON deeper than Python allows and crash the reader. On 2026-11-25 (Python 3.12.3) `json.loads` read 2048 levels of arrays without error and failed at 10000. A frame of at most 4096 bytes holds fewer than 2048 levels, so a frame alone cannot overflow the parser.
- The `RecursionError` is still caught as `BAD_JSON`, in case a later Python lowers its limit; a test forces it. A second test reads the deepest frame that fits.
- Also checked on the same day: `json.loads` accepts `NaN`, `Infinity` and repeated keys, and turns `1e999` into infinity. The last one is not in P-04 of the first draft and is now refused too.

## Note: what 41 mutants of the monitor showed (d248)

- 41 deliberate breaks of the framing, codec, session, buffer and server code. First run: 33 failed a test and 8 survived. After the new tests: 39 fail a test and 2 survive, both equivalent.
- Equivalent, by design (2): matching a time with `\d` instead of `[0-9]`, and `match` instead of `fullmatch`. `datetime.fromisoformat` rejects digits of other scripts and trailing text itself (checked), so the regex is a first layer and the parser a second. The regex stays strict so the rule reads in one place.
- Missing tests (6), now added:
  - Highest common version: with one supported version, highest and lowest are the same. A test now gives the monitor two versions.
  - A gap of exactly one missing reading: the test used a gap of two.
  - A frame that completes late must start a fresh deadline for the next one; a test that sends frame A late and then frame B late.
  - The silence timer must not be reset by bytes of an unfinished frame.
  - A HELLO without a common version closes the connection (only the session was tested, not the server's handling of `close`).
  - Cancellation must be passed on (`task.cancelled()`), not swallowed.
- Pinned limitation: a reading that arrives late, after its successor, is counted as a gap although nothing was lost (`test_pinned_a_late_reading_is_counted_as_a_gap_when_its_successor_came_first`). The monitor cannot tell late from lost at the moment of arrival; the backend sees the full order by `measured_at`.
