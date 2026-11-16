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
