# API: PulseGuard

Conventions of the backend HTTP API. The endpoints and their schemas are in `openapi.yaml` (OpenAPI 3.1, draft); each operation lists the requirements it serves in `x-requirements`.

Last updated: 2026-11-12. `openapi.yaml` passes `openapi-spec-validator` 0.9.0 (23 paths, 25 operations); a broken `$ref` put in on purpose was caught.

## Base and clients

- Base path `/api/v1`. A breaking change gets `/api/v2`; adding a field is not breaking, so clients must ignore fields they do not know.
- Staff use a user token from `POST /auth/login` (JWT, at most 8 hours, REQ-ACC-01). The token carries the user's `token_version`; deactivating a user or changing their role raises it, so old tokens stop working at once (Project 2, R-AUTH-01 there).
- Patient monitors use their own credential, issued once at registration, and may only call `/ingest/*` (REQ-ACC-03). A user token on `/ingest/*`, or a device token anywhere else, gets 403.

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

`GET /sessions/{id}/measurements` returns raw readings while the range holds at most `max_points`; otherwise it returns equal time buckets with `count`, `min`, `max` and `mean`. Min and max are kept so that a short spike that raised an alarm is still visible on the chart (REQ-VIEW-03). Empty buckets are left out, so a gap in the data shows as a gap.

## Ingest

`POST /ingest/batches` takes readings and status events from one patient monitor (ADR-002).

- Each reading is judged on its own: the response lists every item as stored, duplicate or refused with a code. One bad reading never refuses the batch.
- A whole batch is refused only when the request itself is wrong (400) or too large (413); then nothing is stored.
- Sending the same batch again is safe: stored readings come back as duplicates (REQ-MEA-03). A reading with the same seq and other content is refused with `SEQ_CONFLICT` (P-14 in `protocol.md`, R-ORD-04).
- The monitor removes a reading from its buffer only when it appears in `stored`, `duplicates` or `refused`. On a timeout or 5xx it keeps the batch and sends it again (REQ-CON-02, REQ-CON-03).
- Status events carry `event_seq`, per monitor and never reused, so they are idempotent too.
- `GET /ingest/pairings` tells the monitor which sensors it may accept, and when others were unpaired (P-09, P-10 in `protocol.md`). The monitor polls it every 5 s.

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
| `/ingest/*` | No | No | No | No | Own serial |

This table is the input for the role by endpoint matrix test, as in Project 2 (group G there).
