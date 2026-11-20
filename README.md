# PulseGuard

A simulated wireless patient monitoring system for hospital wards, built to practise testing of device communication, alarm logic and patient safety risks.

> PulseGuard is not a medical device and must never be used to monitor a real patient. All data is synthetic. It is not affiliated with or endorsed by any manufacturer.

Status: backend with the health endpoint, the sessions module (register patients, admit, discharge), the device module (register, list, assign, unassign, retire, pairing), measurement ingestion for patient monitors, which authenticate with their own credential, the alarm engine (NEWS2 default limits, debounce, escalation, latching, acknowledgement), staff login with roles and units, and trends and alarm history over a time range. Staff log in with email and password and get a token for 8 hours; patient monitors use their own credential. Technical alarms when a sensor or monitor goes silent (part of d243 in the plan) come with the monitor pipeline in d250. Design documents: [`docs/product-research.md`](docs/product-research.md), [`docs/prd.md`](docs/prd.md), [`docs/risk-analysis.md`](docs/risk-analysis.md), [`docs/architecture.md`](docs/architecture.md), [`docs/protocol.md`](docs/protocol.md), [`docs/database.md`](docs/database.md), [`docs/api.md`](docs/api.md) with [`docs/openapi.yaml`](docs/openapi.yaml), the decisions in [`docs/adr/`](docs/adr/), the learning labs in [`labs/`](labs/) and the defects found in [`docs/findings.md`](docs/findings.md).

## Run with Docker

Needs Docker with Compose.

```bash
cp .env.example .env                    # then set POSTGRES_PASSWORD and PULSEGUARD_JWT_SECRET
docker compose up --build -d
curl http://localhost:8000/api/v1/health
```

The backend applies the migrations when it starts, then serves the API on port 8000. PostgreSQL 17 listens on port 5434 on the host.

A fresh database has no users. Create a unit and the first admin (fake data only):

```bash
docker compose exec backend python -m pulseguard.access.cli create-unit W1 "Ward 1" --beds 12
docker compose exec -e PULSEGUARD_NEW_PASSWORD='a long fake password' backend \
  python -m pulseguard.access.cli create-user admin@example.com "Admin" ADMIN --units W1
curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "a long fake password"}'
```

## Develop locally

Needs Python 3.12 or newer and a PostgreSQL server, for example the `db` service above.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db
export PULSEGUARD_DATABASE_URL=postgresql+psycopg://pulseguard:change-me@localhost:5434/pulseguard
alembic upgrade head
uvicorn pulseguard.main:app --reload
```

Checks:

```bash
ruff check . && ruff format --check .
mypy
export PULSEGUARD_TEST_ADMIN_URL=postgresql+psycopg://pulseguard:change-me@localhost:5434/postgres
pytest
```

Tests marked `db` create and drop their own databases through `PULSEGUARD_TEST_ADMIN_URL` and are skipped when it is not set.
