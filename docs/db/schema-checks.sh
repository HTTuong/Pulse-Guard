#!/usr/bin/env bash
# Loads the draft schema into a fresh database and runs the constraint checks, then a parallel admission check.
# Usage: PGHOST=... PGPORT=... PGUSER=... docs/db/schema-checks.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
db=pulseguard_schema_check
psql -qX -d postgres -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"
psql -qX -d "$db" -v ON_ERROR_STOP=1 -f "$here/schema-draft.sql"
psql -qX -d "$db" -v ON_ERROR_STOP=1 -f "$here/schema-checks.sql" 2>&1 | sed -n 's/^.*NOTICE:  //p'

# REQ-SES-02 under concurrency: 10 admissions of one new patient at the same moment, exactly 1 may succeed.
psql -qX -d "$db" -c "INSERT INTO patient (id, mrn, given_name, family_name) VALUES ('40000000-0000-0000-0000-000000000009', 'TEST-0009', 'Kaisa', 'Example')"
for i in $(seq 1 10); do
  psql -qX -d "$db" -c "INSERT INTO monitoring_session (patient_id, admitted_at, admitted_by) VALUES ('40000000-0000-0000-0000-000000000009', now(), '30000000-0000-0000-0000-000000000001')" >/dev/null 2>&1 &
done
wait
open=$(psql -qXtA -d "$db" -c "SELECT count(*) FROM monitoring_session WHERE patient_id = '40000000-0000-0000-0000-000000000009' AND status = 'OPEN'")
if [ "$open" = "1" ]; then echo "PASS REQ-SES-02 10 parallel admissions: 1 open session"; else echo "FAIL REQ-SES-02 10 parallel admissions: $open open sessions"; fi
