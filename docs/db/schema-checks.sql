-- Checks that the draft schema refuses what the requirements forbid.
-- Run on an empty database after schema-draft.sql; prints one PASS or FAIL line per check.

CREATE OR REPLACE FUNCTION expect_refused(label text, stmt text, want text) RETURNS void AS $$
DECLARE got text;
BEGIN
  BEGIN
    EXECUTE stmt;
    RAISE NOTICE 'FAIL %: accepted, expected %', label, want;
    RETURN;
  EXCEPTION WHEN integrity_constraint_violation OR check_violation OR exclusion_violation OR unique_violation THEN
    GET STACKED DIAGNOSTICS got = CONSTRAINT_NAME;
  END;
  IF got = want THEN RAISE NOTICE 'PASS %: refused by %', label, got;
  ELSE RAISE NOTICE 'FAIL %: refused by %, expected %', label, got, want; END IF;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION expect_ok(label text, stmt text) RETURNS void AS $$
BEGIN
  EXECUTE stmt;
  RAISE NOTICE 'PASS %: accepted', label;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'FAIL %: refused with %', label, SQLERRM;
END $$ LANGUAGE plpgsql;

-- Fake fixed data: ids end in a readable number, names are invented.
INSERT INTO unit (id, code, name) VALUES ('10000000-0000-0000-0000-000000000001', 'W1', 'Ward 1');
INSERT INTO bed (id, unit_id, label) VALUES
  ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', 'B1'),
  ('20000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001', 'B2');
INSERT INTO app_user (id, email, display_name, role, password_hash) VALUES
  ('30000000-0000-0000-0000-000000000001', 'nurse1@example.com', 'Nurse One', 'NURSE', 'x');
INSERT INTO patient (id, mrn, given_name, family_name) VALUES
  ('40000000-0000-0000-0000-000000000001', 'TEST-0001', 'Aino', 'Example'),
  ('40000000-0000-0000-0000-000000000002', 'TEST-0002', 'Eero', 'Example');
INSERT INTO monitoring_session (id, patient_id, admitted_at, admitted_by) VALUES
  ('50000000-0000-0000-0000-00000000000a', '40000000-0000-0000-0000-000000000001', '2026-11-12T08:00Z', '30000000-0000-0000-0000-000000000001'),
  ('50000000-0000-0000-0000-00000000000b', '40000000-0000-0000-0000-000000000002', '2026-11-12T08:00Z', '30000000-0000-0000-0000-000000000001');
INSERT INTO bed_stay (session_id, bed_id, from_at) VALUES
  ('50000000-0000-0000-0000-00000000000a', '20000000-0000-0000-0000-000000000001', '2026-11-12T08:00Z');
INSERT INTO device (id, serial, kind, registered_at) VALUES
  ('60000000-0000-0000-0000-000000000001', 'SN-PO-0001', 'PULSE_OXIMETER', '2026-11-01T00:00Z');

-- Sessions
SELECT expect_refused('REQ-SES-02 second open session of a patient',
  $s$INSERT INTO monitoring_session (patient_id, admitted_at, admitted_by) VALUES ('40000000-0000-0000-0000-000000000001', '2026-11-12T09:00Z', '30000000-0000-0000-0000-000000000001')$s$,
  'session_one_open_per_patient_uq');
SELECT expect_refused('REQ-SES-01 bed already holds a session',
  $s$INSERT INTO bed_stay (session_id, bed_id, from_at) VALUES ('50000000-0000-0000-0000-00000000000b', '20000000-0000-0000-0000-000000000001', '2026-11-12T09:00Z')$s$,
  'bed_stay_no_overlap_per_bed');
SELECT expect_refused('REQ-SES-04 session in two beds at once',
  $s$INSERT INTO bed_stay (session_id, bed_id, from_at) VALUES ('50000000-0000-0000-0000-00000000000a', '20000000-0000-0000-0000-000000000002', '2026-11-12T09:00Z')$s$,
  'bed_stay_no_overlap_per_session');
SELECT expect_ok('REQ-SES-04 move A out of B1 at 10:00',
  $s$UPDATE bed_stay SET to_at = '2026-11-12T10:00Z' WHERE session_id = '50000000-0000-0000-0000-00000000000a'$s$);
SELECT expect_ok('REQ-SES-04 A into B2 from exactly 10:00',
  $s$INSERT INTO bed_stay (session_id, bed_id, from_at) VALUES ('50000000-0000-0000-0000-00000000000a', '20000000-0000-0000-0000-000000000002', '2026-11-12T10:00Z')$s$);
SELECT expect_ok('REQ-SES-01 B into B1 from exactly 10:00',
  $s$INSERT INTO bed_stay (session_id, bed_id, from_at) VALUES ('50000000-0000-0000-0000-00000000000b', '20000000-0000-0000-0000-000000000001', '2026-11-12T10:00Z')$s$);
SELECT expect_refused('REQ-SES-03 closed session without who discharged',
  $s$UPDATE monitoring_session SET status = 'CLOSED', discharged_at = '2026-11-12T10:00Z' WHERE id = '50000000-0000-0000-0000-00000000000b'$s$,
  'session_closed_fields_ck');

-- Devices: A until 10:00, then B from 10:00 is allowed; overlap is not
SELECT expect_ok('REQ-DEV-02 assign to session A',
  $s$INSERT INTO device_assignment (device_id, session_id, assigned_at, assigned_by) VALUES ('60000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-00000000000a', '2026-11-12T08:00Z', '30000000-0000-0000-0000-000000000001')$s$);
SELECT expect_refused('REQ-DEV-02 same device to session B while assigned to A',
  $s$INSERT INTO device_assignment (device_id, session_id, assigned_at, assigned_by) VALUES ('60000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-00000000000b', '2026-11-12T09:00Z', '30000000-0000-0000-0000-000000000001')$s$,
  'assignment_no_overlap_per_device');
SELECT expect_ok('REQ-DEV-03 unassign from A at 10:00',
  $s$UPDATE device_assignment SET unassigned_at = '2026-11-12T10:00Z', unassigned_by = '30000000-0000-0000-0000-000000000001' WHERE session_id = '50000000-0000-0000-0000-00000000000a'$s$);
SELECT expect_ok('REQ-DEV-02 assign to B from exactly 10:00',
  $s$INSERT INTO device_assignment (device_id, session_id, assigned_at, assigned_by) VALUES ('60000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-00000000000b', '2026-11-12T10:00Z', '30000000-0000-0000-0000-000000000001')$s$);
SELECT expect_refused('REQ-DEV-01 duplicate serial',
  $s$INSERT INTO device (serial, kind, registered_at) VALUES ('SN-PO-0001', 'PULSE_OXIMETER', '2026-11-01T00:00Z')$s$,
  'device_serial_key');
SELECT expect_refused('REQ-ACC-03 patient monitor without a credential',
  $s$INSERT INTO device (serial, kind, registered_at) VALUES ('SN-PM-0001', 'PATIENT_MONITOR', '2026-11-01T00:00Z')$s$,
  'device_monitor_credential_ck');

-- REQ-MEA-06: the session of a reading is the one its device had at the measured time
DO $$
DECLARE before_move uuid; after_move uuid;
BEGIN
  SELECT session_id INTO before_move FROM device_assignment
   WHERE device_id = '60000000-0000-0000-0000-000000000001'
     AND tstzrange(assigned_at, coalesce(unassigned_at, 'infinity'), '[)') @> '2026-11-12T09:59:59.999Z'::timestamptz;
  SELECT session_id INTO after_move FROM device_assignment
   WHERE device_id = '60000000-0000-0000-0000-000000000001'
     AND tstzrange(assigned_at, coalesce(unassigned_at, 'infinity'), '[)') @> '2026-11-12T10:00:00.000Z'::timestamptz;
  IF before_move = '50000000-0000-0000-0000-00000000000a' AND after_move = '50000000-0000-0000-0000-00000000000b'
  THEN RAISE NOTICE 'PASS REQ-MEA-06 reading at 09:59:59.999 goes to A, at 10:00 to B';
  ELSE RAISE NOTICE 'FAIL REQ-MEA-06 got % and %', before_move, after_move; END IF;
END $$;

-- Measurements
SELECT expect_ok('REQ-MEA-01 first reading',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 41, 'PR', 72, '2026-11-12T09:00Z', '2026-11-12T09:00:01Z')$s$);
SELECT expect_refused('REQ-MEA-03 same device and seq stored twice',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 41, 'PR', 72, '2026-11-12T09:00Z', '2026-11-12T09:00:05Z')$s$,
  'measurement_device_seq_key');
SELECT expect_refused('REQ-MEA-03 same device and seq with other content (R-ORD-04)',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 41, 'PR', 88, '2026-11-12T09:30Z', '2026-11-12T09:30:01Z')$s$,
  'measurement_device_seq_key');
SELECT expect_refused('REQ-MEA-02 SpO2 that is not a whole number',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 42, 'SPO2', 95.5, '2026-11-12T09:00Z', '2026-11-12T09:00:01Z')$s$,
  'measurement_plausible_ck');
SELECT expect_refused('REQ-MEA-02 pulse rate 301',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 43, 'PR', 301, '2026-11-12T09:00Z', '2026-11-12T09:00:01Z')$s$,
  'measurement_plausible_ck');
SELECT expect_ok('REQ-MEA-02 pulse rate 300 is the upper edge',
  $s$INSERT INTO measurement (session_id, device_id, seq, parameter, value, measured_at, received_at) VALUES ('50000000-0000-0000-0000-00000000000a', '60000000-0000-0000-0000-000000000001', 44, 'PR', 300, '2026-11-12T09:00Z', '2026-11-12T09:00:01Z')$s$);

-- Limits
SELECT expect_ok('REQ-ALM-06 default pulse limits 40, 111, 131',
  $s$INSERT INTO session_limit VALUES ('50000000-0000-0000-0000-00000000000a', 'PR', 40, NULL, 111, 131, '2026-11-12T08:00Z', NULL)$s$);
SELECT expect_refused('REQ-ALM-06 medium limit above the high limit',
  $s$UPDATE session_limit SET medium_high = 140 WHERE session_id = '50000000-0000-0000-0000-00000000000a' AND parameter = 'PR'$s$,
  'session_limit_order_ck');

-- Alarms
SELECT expect_ok('REQ-ALM-01 first PR_HIGH alarm',
  $s$INSERT INTO alarm (id, session_id, kind, code, parameter, priority, started_at) VALUES ('70000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-00000000000a', 'PHYSIOLOGICAL', 'PR_HIGH', 'PR', 'HIGH', '2026-11-12T09:00:06Z')$s$);
SELECT expect_refused('REQ-ALM-02 second open PR_HIGH alarm for the same session',
  $s$INSERT INTO alarm (session_id, kind, code, parameter, priority, started_at) VALUES ('50000000-0000-0000-0000-00000000000a', 'PHYSIOLOGICAL', 'PR_HIGH', 'PR', 'HIGH', '2026-11-12T09:00:20Z')$s$,
  'alarm_one_open_per_condition_uq');
SELECT expect_refused('REQ-ALM-03 acknowledged without who',
  $s$UPDATE alarm SET state = 'ACKNOWLEDGED', acknowledged_at = '2026-11-12T09:01Z' WHERE id = '70000000-0000-0000-0000-000000000001'$s$,
  'alarm_ack_pair_ck');
SELECT expect_refused('REQ-ALM-03 resolved without a reason',
  $s$UPDATE alarm SET state = 'RESOLVED', resolved_at = '2026-11-12T09:01Z' WHERE id = '70000000-0000-0000-0000-000000000001'$s$,
  'alarm_state_fields_ck');
SELECT expect_ok('REQ-ALM-03 resolve with a reason',
  $s$UPDATE alarm SET state = 'RESOLVED', resolved_at = '2026-11-12T09:01Z', resolution = 'DISCHARGED' WHERE id = '70000000-0000-0000-0000-000000000001'$s$);
SELECT expect_ok('REQ-ALM-02 a new PR_HIGH alarm after the first is resolved',
  $s$INSERT INTO alarm (session_id, kind, code, parameter, priority, started_at) VALUES ('50000000-0000-0000-0000-00000000000a', 'PHYSIOLOGICAL', 'PR_HIGH', 'PR', 'HIGH', '2026-11-12T09:05Z')$s$);
SELECT expect_ok('REQ-ALM-03 acknowledge the new alarm',
  $s$UPDATE alarm SET state = 'ACKNOWLEDGED', acknowledged_at = '2026-11-12T09:06Z', acknowledged_by = '30000000-0000-0000-0000-000000000001' WHERE code = 'PR_HIGH' AND state = 'ACTIVE'$s$);
SELECT expect_refused('REQ-ALM-02 another PR_HIGH alarm while one is acknowledged',
  $s$INSERT INTO alarm (session_id, kind, code, parameter, priority, started_at) VALUES ('50000000-0000-0000-0000-00000000000a', 'PHYSIOLOGICAL', 'PR_HIGH', 'PR', 'HIGH', '2026-11-12T09:07Z')$s$,
  'alarm_one_open_per_condition_uq');
SELECT expect_refused('REQ-ALM-05 technical alarm without its device',
  $s$INSERT INTO alarm (session_id, kind, code, priority, started_at) VALUES ('50000000-0000-0000-0000-00000000000a', 'TECHNICAL', 'SENSOR_LOST', 'MEDIUM', '2026-11-12T09:05Z')$s$,
  'alarm_kind_fields_ck');
