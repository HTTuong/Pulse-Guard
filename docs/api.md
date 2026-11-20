# API: PulseGuard

Conventions of the backend HTTP API. The endpoints and their schemas are in `openapi.yaml` (OpenAPI 3.1, draft); each operation lists the requirements it serves in `x-requirements`.

Last updated: 2026-11-18. `openapi.yaml` passes `openapi-spec-validator` 0.9.0 (23 paths, 25 operations); a broken `$ref` put in on purpose was caught.

## Base and clients

- Base path `/api/v1`. A breaking change gets `/api/v2`; adding a field is not breaking, so clients must ignore fields they do not know.
- Staff use a user token from `POST /auth/login` (JWT, at most 8 hours, REQ-ACC-01). The token carries the user's `token_version`; deactivating a user or changing their role raises it, so old tokens stop working at once (Project 2, R-AUTH-01 there).
- Patient monitors use their own credential, issued once at registration, and may only call `/ingest/*` (REQ-ACC-03). A user token on `/ingest/*`, or a device token anywhere else, gets 403.
- The header must be exactly `Authorization: Bearer <token>`; another scheme, a second word or an extra space is 401.
- A token is HS256 with issuer `pulseguard` and audience `pulseguard-staff`, and holds only the user id and `token_version`. Role and units are read from the database on every request, so moving a nurse to another unit applies at once, without a new token. A deactivated user is refused even when the deactivation was made in the database without raising `token_version`.
- Expiry, issue time and the 8 hour cap are checked against the server clock; a token that claims a longer life, or was issued in the future, is refused.
- `POST /auth/login` answers 401 with the same body for an unknown email, a wrong password and a deactivated user, and runs the password hash for an unknown email too, so the time of the answer does not tell which emails exist. Emails are compared without case.
- The backend refuses to start without `PULSEGUARD_JWT_SECRET` of at least 32 characters.
- Not built yet: limits on login attempts (R-AUTH-01 in `risk-analysis.md`).

## Times

- Every time in a response is UTC with milliseconds and `Z`: `2026-11-12T18:52:03.000Z`.
- A time in a request must have an offset; one without is refused with 400 `INVALID_TIME`, never read as local time.
- `from` and `to` ranges are half open, `[from, to)`. `from` must be before `to` and the range at most 7 days, otherwise 400 `INVALID_RANGE`.

## Errors

Errors use Problem Details (RFC 9457), content type `application/problem+json`. `code` is stable and is what clients and tests check; `title` and `detail` are for people and may change.

```json
{"type":"/problems/bed-occupied","title":"Bed is occupied","status":409,"code":"BED_OCCUPIED","detail":"Bed B1 of W1 has an open session"}
```

| Status | When | Codes |
|---|---|---|
| 400 | The request itself is wrong; `errors` lists each field | `VALIDATION_FAILED`, `INVALID_TIME`, `INVALID_RANGE`, `INVALID_CURSOR` |
| 401 | No token, a bad one or an expired one | `UNAUTHENTICATED` |
| 403 | Valid token, but the role or unit does not allow it; nothing changed | `FORBIDDEN` |
| 404 | No such resource | `NOT_FOUND` |
| 409 | The request is fine but the current state does not allow it | `PATIENT_HAS_OPEN_SESSION`, `BED_OCCUPIED`, `SESSION_CLOSED`, `DEVICE_NOT_AVAILABLE`, `DEVICE_ASSIGNED`, `DEVICE_RETIRED`, `SESSION_HAS_DEVICE_KIND`, `SERIAL_TAKEN`, `MRN_TAKEN`, `ALARM_NOT_ACTIVE`, `LIMIT_OUTSIDE_ABSOLUTE` |
| 413 | Ingest batch over 1000 readings or 1 MB | `BATCH_TOO_LARGE` |
| 500 | A bug; the body never contains a stack trace or SQL | `INTERNAL` |

- A constraint violation from the database is mapped to its 409 code, never shown as 500; the constraint names in `database.md` are what the mapping uses.
- The order of checks is fixed: authentication (401), then input (400), then existence (404), then permission (403), then state (409). The same request gets the same status every time, so tests can rely on it.
- Resources of another unit get 403, not 404, as REQ-ACC-02 says. This tells a caller that the id exists; accepted, because ids are random UUIDs and staff are trusted users.

## Pagination

- Lists that grow use cursor pagination: `?limit=50` (1 to 500), and `next_cursor` from the response for the next page; `next_cursor` is `null` on the last page.
- The cursor holds the sort key of the last item plus its id as a tie-breaker, so a page is stable while new readings and alarms keep arriving. With offset pagination, a new row at the front pushes rows across a page boundary, and a client sees one twice or misses one.
- Each list has one fixed order, given in `openapi.yaml`. A cursor from one query used with other filters gets 400 `INVALID_CURSOR`.
- `GET /overview` and `GET /sessions/{id}/limits` are not paginated: they are small and must be read as one consistent snapshot.

## Downsampling

`GET /sessions/{id}/measurements?parameter=PR&from=...&to=...` returns raw readings while the range holds at most `max_points` (10 to 2000, default 500); otherwise it returns equal time buckets with `count`, `min`, `max` and `mean`. Min and max are kept so that a short spike that raised an alarm is still visible on the chart (REQ-VIEW-03). Empty buckets are left out, so a gap in the data shows as a gap.

- `total` is the number of readings in the range; `downsampled` says which of the two forms came back.
- Bucket widths are whole microseconds, rounded up, so there are never more buckets than `max_points`. Bucket edges are exact: each bucket ends where the next one starts, the last one ends at `to`.
- Raw readings are paginated with a cursor on (measured time, id), so readings measured at the same moment are neither skipped nor repeated, and a late reading stored behind the cursor does not shift the next page. Buckets come in one response; a cursor with downsampled data is 400 `INVALID_CURSOR`.
- `GET /sessions/{id}/alarms?from=...&to=...` lists the alarms that overlap the range: started before `to` and not resolved before `from`. An alarm still open is in every later range; one resolved exactly at `from` is included, one starting exactly at `to` is not.
- Both work on closed sessions too, for nurses and physicians of the session's unit.
- The history of every alarm (`GET /alarms/{id}`) is a chain of allowed steps that ends in the alarm's current state, priority, start, resolution and acknowledgement times. `alarms/history.py` names each way it could break; the tests check every alarm they create, including seeded random scenarios.

## Ingest

`POST /ingest/batches` takes readings and status events from one patient monitor (ADR-002).

- Each reading is judged on its own: the response lists every item as stored, duplicate or refused with a code. One bad reading never refuses the batch.
- A whole batch is refused only when the request itself is wrong (400) or too large (413); then nothing is stored.
- Sending the same batch again is safe: stored readings come back as duplicates (REQ-MEA-03). A reading with the same seq and other content is refused with `SEQ_CONFLICT` (P-14 in `protocol.md`, R-ORD-04).
- The monitor removes a reading from its buffer only when it appears in `stored`, `duplicates` or `refused`. On a timeout or 5xx it keeps the batch and sends it again (REQ-CON-02, REQ-CON-03).
- Checks of one reading, in this order, each with its code: the sensor is known (`UNKNOWN_DEVICE`), not retired (`DEVICE_RETIRED`), measures this parameter (`WRONG_PARAMETER`), uses its unit (`BAD_UNIT`), has a measured time with an offset inside the window (`BAD_TIME`), has a plausible value (`OUT_OF_RANGE`), was assigned to a session at the measured time (`NO_SESSION`), and the calling monitor was in that same session at that time (`NOT_PAIRED`).
- A retired sensor's readings are refused even when they were measured while it was still in use.
- Values are kept with one decimal, rounded half away from zero as PostgreSQL does. Ranges and duplicates are judged on that stored value: 72.04 is stored as 72.0 and is a duplicate of 72.0; 300.05 becomes 300.1 and is out of range.
- `NaN`, `Infinity` and `-Infinity` are read by Python's JSON parser without an error, and are refused as `OUT_OF_RANGE`.
- At the exact moment a sensor moves, its readings belong to the new session (ranges are half open).
- Status events carry `event_seq`, per monitor and never reused, so they are idempotent too. Until they are handled with technical alarms, a batch with any event is refused with 400 rather than storing the readings and dropping the events in silence.
- `GET /ingest/pairings` tells the monitor which sensors it may accept, and when others were unpaired (P-09, P-10 in `protocol.md`). The monitor polls it every 5 s.

## Alarms

- `POST /alarms/{id}/acknowledge`: nurses and physicians of the patient's unit. An ACTIVE alarm becomes ACKNOWLEDGED; an ACTIVE alarm whose condition has already cleared becomes RESOLVED (`ACKNOWLEDGED_AFTER_CLEAR`). Anything else is 409 `ALARM_NOT_ACTIVE`. The alarm row is locked, so of several people pressing at once exactly one succeeds.
- `GET /alarms/{id}` returns the alarm with its full history: every change, its time, and the user when a person made it.
- `GET /sessions/{id}/limits` returns the session's limits; admission writes the defaults.
- Engine changes are timed by the measured time of the reading that caused them; changes made by people are timed by the server clock.
- `GET /auth/me` returns the caller's id, role and units. Admins create users with `POST /users` and change them with `PATCH /users/{id}` (active, role, units); an admin may not deactivate or demote themselves (409 `SELF_LOCKOUT`).
- Not built yet: changing limits (REQ-ALM-06), escalation after 120 s (REQ-ALM-07), technical alarms (REQ-ALM-05) and the list of open alarms for the central view.

## Who may call what

| Endpoint group | Nurse | Physician | Technician | Admin | Patient monitor |
|---|---|---|---|---|---|
| Admit, discharge, move, register patient | Own units | No | No | No | No |
| Read sessions, overview, history, alarms | Own units | Own units | No | No | No |
| Acknowledge alarms, change limits | Own units | Own units | No | No | No |
| Register, retire devices | No | No | Yes | No | No |
| Assign, unassign devices | Own units | No | Yes | No | No |
| List devices (no vital signs) | Own units | No | Yes | No | No |
| Units, users, absolute limits | No | No | No | Yes | No |
| `/auth/me` | Yes | Yes | Yes | Yes | No |
| `/ingest/*` | No | No | No | No | Own serial |

This table is the input for the role by endpoint matrix test, as in Project 2 (group G there): `tests/access/test_matrix.py` checks 18 routes for 7 callers (5 staff, no token, a monitor's credential), and that no refused request changed anything. Patients and devices belong to no unit, so any nurse may register a patient or list devices.
