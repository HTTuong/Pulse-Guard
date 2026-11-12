-- PulseGuard database schema, draft for d238.
-- The Alembic migrations (d239 onward) are the source of truth once they exist; this file is the design they follow.
-- Every constraint that a rule depends on is named, so a test can assert which one refused a write.

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TYPE user_role AS ENUM ('NURSE', 'PHYSICIAN', 'TECHNICIAN', 'ADMIN');
CREATE TYPE session_status AS ENUM ('OPEN', 'CLOSED');
CREATE TYPE device_kind AS ENUM ('PATIENT_MONITOR', 'PULSE_OXIMETER', 'RESPIRATION');
CREATE TYPE device_status AS ENUM ('AVAILABLE', 'ASSIGNED', 'RETIRED');
CREATE TYPE parameter AS ENUM ('PR', 'SPO2', 'RR');
CREATE TYPE alarm_kind AS ENUM ('PHYSIOLOGICAL', 'TECHNICAL');
CREATE TYPE alarm_priority AS ENUM ('MEDIUM', 'HIGH');
CREATE TYPE alarm_state AS ENUM ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED');
CREATE TYPE connection_state AS ENUM ('CONNECTED', 'LOST');

-- Units, beds and users

CREATE TABLE unit (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code        text NOT NULL CONSTRAINT unit_code_key UNIQUE,
  name        text NOT NULL,
  time_zone   text NOT NULL DEFAULT 'Europe/Helsinki'
);

CREATE TABLE bed (
  id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  unit_id  uuid NOT NULL REFERENCES unit(id),
  label    text NOT NULL,
  CONSTRAINT bed_unit_label_key UNIQUE (unit_id, label)
);

CREATE TABLE app_user (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL CONSTRAINT app_user_email_key UNIQUE,
  display_name   text NOT NULL,
  role           user_role NOT NULL,
  password_hash  text NOT NULL,
  active         boolean NOT NULL DEFAULT true,
  token_version  integer NOT NULL DEFAULT 0  -- raised on deactivation or role change, so old tokens stop working
);

CREATE TABLE user_unit (
  user_id  uuid NOT NULL REFERENCES app_user(id),
  unit_id  uuid NOT NULL REFERENCES unit(id),
  PRIMARY KEY (user_id, unit_id)
);

-- Patients and sessions (REQ-SES)

CREATE TABLE patient (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mrn           text NOT NULL CONSTRAINT patient_mrn_key UNIQUE,  -- synthetic record number, never a real one
  given_name    text NOT NULL,
  family_name   text NOT NULL,
  birth_year    integer CHECK (birth_year BETWEEN 1900 AND 2100)
);

CREATE TABLE monitoring_session (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id     uuid NOT NULL REFERENCES patient(id),
  status         session_status NOT NULL DEFAULT 'OPEN',
  admitted_at    timestamptz NOT NULL,
  admitted_by    uuid NOT NULL REFERENCES app_user(id),
  discharged_at  timestamptz,
  discharged_by  uuid REFERENCES app_user(id),
  CONSTRAINT session_closed_fields_ck CHECK (
    (status = 'OPEN' AND discharged_at IS NULL AND discharged_by IS NULL) OR
    (status = 'CLOSED' AND discharged_at IS NOT NULL AND discharged_by IS NOT NULL AND discharged_at >= admitted_at))
);
-- REQ-SES-02: at most one open session per patient, also under parallel admissions (R-ISO-02).
CREATE UNIQUE INDEX session_one_open_per_patient_uq ON monitoring_session (patient_id) WHERE status = 'OPEN';

-- Where a session was over time; a move closes one stay and opens the next (REQ-SES-04).
CREATE TABLE bed_stay (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id  uuid NOT NULL REFERENCES monitoring_session(id),
  bed_id      uuid NOT NULL REFERENCES bed(id),
  from_at     timestamptz NOT NULL,
  to_at       timestamptz,
  CONSTRAINT bed_stay_order_ck CHECK (to_at IS NULL OR to_at > from_at),
  -- REQ-SES-01: a bed holds one session at a time.
  CONSTRAINT bed_stay_no_overlap_per_bed EXCLUDE USING gist (
    bed_id WITH =, tstzrange(from_at, coalesce(to_at, 'infinity'), '[)') WITH &&),
  -- A session is in one bed at a time.
  CONSTRAINT bed_stay_no_overlap_per_session EXCLUDE USING gist (
    session_id WITH =, tstzrange(from_at, coalesce(to_at, 'infinity'), '[)') WITH &&)
);

-- Devices (REQ-DEV)

CREATE TABLE device (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  serial           text NOT NULL CONSTRAINT device_serial_key UNIQUE,
  kind             device_kind NOT NULL,
  status           device_status NOT NULL DEFAULT 'AVAILABLE',
  credential_hash  text,  -- patient monitors only: they authenticate to the ingest API (REQ-ACC-03)
  registered_at    timestamptz NOT NULL,
  retired_at       timestamptz,
  CONSTRAINT device_monitor_credential_ck CHECK ((kind = 'PATIENT_MONITOR') = (credential_hash IS NOT NULL)),
  CONSTRAINT device_retired_fields_ck CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL))
);

-- Assignment history. Kept, not overwritten, because a late reading is matched to the session
-- its device had at the measured time (REQ-MEA-06, R-ISO-01).
CREATE TABLE device_assignment (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id       uuid NOT NULL REFERENCES device(id),
  session_id      uuid NOT NULL REFERENCES monitoring_session(id),
  assigned_at     timestamptz NOT NULL,
  assigned_by     uuid NOT NULL REFERENCES app_user(id),
  unassigned_at   timestamptz,
  unassigned_by   uuid REFERENCES app_user(id),
  CONSTRAINT assignment_order_ck CHECK (unassigned_at IS NULL OR unassigned_at > assigned_at),
  -- REQ-DEV-02: one session per device at any instant, under concurrency too (R-ISO-02).
  CONSTRAINT assignment_no_overlap_per_device EXCLUDE USING gist (
    device_id WITH =, tstzrange(assigned_at, coalesce(unassigned_at, 'infinity'), '[)') WITH &&)
);
-- "One monitor and one sensor per parameter per session" spans two tables (kind lives on device),
-- so it is checked in code inside the assigning transaction, with the session row locked.

CREATE TABLE device_connection (
  device_id     uuid PRIMARY KEY REFERENCES device(id),
  state         connection_state NOT NULL,
  since         timestamptz NOT NULL,
  last_seen_at  timestamptz NOT NULL
);

-- Measurements (REQ-MEA)

CREATE TABLE measurement (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id   uuid NOT NULL REFERENCES monitoring_session(id),
  device_id    uuid NOT NULL REFERENCES device(id),
  seq          bigint NOT NULL CHECK (seq >= 1),
  parameter    parameter NOT NULL,
  value        numeric(5,1) NOT NULL,
  measured_at  timestamptz NOT NULL,
  received_at  timestamptz NOT NULL,
  -- REQ-MEA-03: one stored reading per device and sequence number (R-ORD-01).
  CONSTRAINT measurement_device_seq_key UNIQUE (device_id, seq),
  -- REQ-MEA-02: plausible ranges, a second line behind the API's validation.
  CONSTRAINT measurement_plausible_ck CHECK (
    (parameter = 'RR'   AND value BETWEEN 0 AND 120) OR
    (parameter = 'SPO2' AND value BETWEEN 0 AND 100 AND value = trunc(value)) OR
    (parameter = 'PR'   AND value BETWEEN 0 AND 300))
);
-- History and current value are read by measured time (REQ-MEA-04).
CREATE INDEX measurement_session_param_time_idx ON measurement (session_id, parameter, measured_at DESC, id DESC);

-- Refused readings are counted per device and reason (REQ-MEA-02, P-14 SEQ_CONFLICT).
CREATE TABLE reading_rejection (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  device_serial  text NOT NULL,  -- text, not a foreign key: unknown devices are logged too
  seq            bigint,
  code           text NOT NULL,
  detail         text,
  received_at    timestamptz NOT NULL
);
CREATE INDEX reading_rejection_device_idx ON reading_rejection (device_serial, received_at DESC);

-- Alarm limits (REQ-ALM-06)

CREATE TABLE absolute_limit (
  parameter  parameter PRIMARY KEY,
  min_value  numeric(5,1) NOT NULL,
  max_value  numeric(5,1) NOT NULL,
  CONSTRAINT absolute_limit_order_ck CHECK (min_value < max_value)
);

-- Limits of one session. A value at or below high_low, or at or above high_high, is high priority;
-- at or below medium_low, or at or above medium_high, is medium priority. NULL means no alarm on that side.
CREATE TABLE session_limit (
  session_id   uuid NOT NULL REFERENCES monitoring_session(id),
  parameter    parameter NOT NULL,
  high_low     numeric(5,1),
  medium_low   numeric(5,1),
  medium_high  numeric(5,1),
  high_high    numeric(5,1),
  changed_at   timestamptz NOT NULL,
  changed_by   uuid REFERENCES app_user(id),  -- NULL for the defaults set at admission
  PRIMARY KEY (session_id, parameter),
  -- Set limits must go strictly up: high_low < medium_low < medium_high < high_high, skipping NULLs.
  CONSTRAINT session_limit_order_ck CHECK (
    (high_low IS NULL OR medium_low IS NULL OR high_low < medium_low) AND
    (high_low IS NULL OR medium_high IS NULL OR high_low < medium_high) AND
    (high_low IS NULL OR high_high IS NULL OR high_low < high_high) AND
    (medium_low IS NULL OR medium_high IS NULL OR medium_low < medium_high) AND
    (medium_low IS NULL OR high_high IS NULL OR medium_low < high_high) AND
    (medium_high IS NULL OR high_high IS NULL OR medium_high < high_high))
);

CREATE TABLE session_limit_change (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id   uuid NOT NULL REFERENCES monitoring_session(id),
  parameter    parameter NOT NULL,
  old_limits   jsonb NOT NULL,
  new_limits   jsonb NOT NULL,
  changed_at   timestamptz NOT NULL,
  changed_by   uuid NOT NULL REFERENCES app_user(id)
);

-- Alarms (REQ-ALM)

CREATE TABLE alarm (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         uuid NOT NULL REFERENCES monitoring_session(id),
  kind               alarm_kind NOT NULL,
  code               text NOT NULL,  -- PR_HIGH, PR_LOW, SPO2_LOW, RR_HIGH, RR_LOW, SENSOR_LOST, MONITOR_LOST
  parameter          parameter,
  device_id          uuid REFERENCES device(id),
  priority           alarm_priority NOT NULL,
  state              alarm_state NOT NULL DEFAULT 'ACTIVE',
  condition_cleared  boolean NOT NULL DEFAULT false,
  escalated          boolean NOT NULL DEFAULT false,
  started_at         timestamptz NOT NULL,  -- measured time of the reading that completed the debounce
  acknowledged_at    timestamptz,
  acknowledged_by    uuid REFERENCES app_user(id),
  resolved_at        timestamptz,
  resolution         text,  -- ACKNOWLEDGED_AFTER_CLEAR, AUTO_RESOLVED, DATA_BACK, DISCHARGED
  CONSTRAINT alarm_kind_fields_ck CHECK (
    (kind = 'PHYSIOLOGICAL' AND parameter IS NOT NULL) OR
    (kind = 'TECHNICAL' AND device_id IS NOT NULL)),
  CONSTRAINT alarm_state_fields_ck CHECK (
    (state = 'ACTIVE' AND acknowledged_at IS NULL AND resolved_at IS NULL) OR
    (state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL AND resolved_at IS NULL) OR
    (state = 'RESOLVED' AND resolved_at IS NOT NULL AND resolution IS NOT NULL)),
  CONSTRAINT alarm_ack_pair_ck CHECK ((acknowledged_at IS NULL) = (acknowledged_by IS NULL))
);
-- REQ-ALM-02: one alarm per condition while it is not resolved (R-ALM-04).
CREATE UNIQUE INDEX alarm_one_open_per_condition_uq ON alarm (session_id, code) WHERE state <> 'RESOLVED';
CREATE INDEX alarm_open_idx ON alarm (priority DESC, started_at, id) WHERE state <> 'RESOLVED';

CREATE TABLE alarm_event (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  alarm_id    uuid NOT NULL REFERENCES alarm(id),
  at          timestamptz NOT NULL,
  from_state  alarm_state,
  to_state    alarm_state NOT NULL,
  priority    alarm_priority NOT NULL,
  by_user     uuid REFERENCES app_user(id),  -- NULL when the engine made the change
  reason      text NOT NULL
);
CREATE INDEX alarm_event_alarm_idx ON alarm_event (alarm_id, at, id);
