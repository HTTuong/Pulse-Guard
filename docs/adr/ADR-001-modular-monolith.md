# ADR-001: One backend as a modular monolith

Status: Accepted. Date: 2026-11-12.

## Context

The backend owns sessions, devices, measurements, alarms and access. Several rules cross these areas in one step: a reading must be matched to the session its sensor had at the measured time, stored once, and fed to the alarm engine; a discharge must close the session, unassign its devices and resolve its alarms together (REQ-SES-03, REQ-MEA-06).

The project is built and tested by one person, and the goal is to test the system well, not to run infrastructure.

## Options

| Option | For | Against |
|---|---|---|
| Microservices: separate services for ingest, alarms, sessions and devices, talking over a queue or HTTP | Each service scales and fails alone; realistic for large vendors | Cross-area rules need distributed transactions or eventual consistency, which adds new ways to show stale or mixed data (R-ISO-01, R-STALE-02); many more moving parts to start, deploy and debug in CI |
| Modular monolith: one process and one database, a module per area with a public interface | A discharge is one database transaction; uniqueness can be a database constraint; one thing to start in tests and CI | One process is one point of failure; module borders are kept by discipline, not by the network |
| Plain monolith with no module borders | Fastest to start | Rules leak across areas and the code gets hard to test in parts |

## Decision

A modular monolith in one FastAPI process with one PostgreSQL database. Modules: sessions, devices, ingest, measurements, alarms, access. A module only calls another module's public functions and never reads its tables. The alarm engine is a pure module with no I/O.

The sensor simulator and the patient monitor stay separate processes, because in the real world they are separate devices, and faults between them are what we want to test.

## Consequences

- Discharge, move and ingest can each run in one transaction, so a half done state is not possible (R-ISO-01, R-STALE-02).
- Concurrency risks are handled by database constraints and tested with parallel requests (R-ISO-02).
- A backend restart stops everything; alarm state must be stored, not kept in memory (R-REC-04).
- If a module border is broken it does not fail loudly. An import check in the lint step will refuse imports of another module's internals.

## How we will know

- A test discharges a session with 2 devices and 1 active alarm and checks all three changes happened, or none after an injected failure.
- The import check runs in CI.
