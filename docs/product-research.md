# Product Research: Wireless Patient Monitoring on Hospital Wards

The research behind PulseGuard: what real wireless ward monitoring products do, the clinical workflow they support, and what PulseGuard will model. It is the input for the requirements (`prd.md`) and the risk analysis (`risk-analysis.md`).

Researched on 2026-11-11 from public sources only. Product names belong to their owners; GE HealthCare, Portrait Mobile, Portrait VSM and CARESCAPE are trademarks of GE HealthCare.

## Disclaimer

PulseGuard is a simulated system built to practise testing. It is not a medical device, it is not affiliated with or endorsed by GE HealthCare or any manufacturer, and it must never be used to monitor a real patient.

All patient data in PulseGuard is synthetic: generated names, generated vital signs from seeded simulators, no data from any real person or device.

Thresholds and clinical rules in PulseGuard are design assumptions for testing, taken from public guidance where possible. They are not clinical advice.

## Why continuous monitoring on wards

- On general wards, vital signs are usually checked by hand every four to six hours, so a patient can deteriorate between two checks unnoticed ([MedTech Dive](https://www.medtechdive.com/news/ge-healthcare-fda-clearance-wireless-vital-sign-monitor/690988/)).
- Wireless wearable monitoring aims at patients in "critical recovery periods, especially after surgery or discharge from the intensive care unit", who can walk around the ward instead of being tied to a bed ([Business Wire, 2023](https://www.businesswire.com/news/home/20230814055987/en)).
- The risk on the other side is alarm fatigue: too many alarms make staff miss valid ones. ECRI ranked alarm hazards first in its 2015 technology hazards list, and The Joint Commission made alarm management a National Patient Safety Goal (NPSG.06.0101) ([GE HealthCare, alarm management](https://www.gehealthcare.com/en-in/products/patient-monitoring/alarm-management)).

## What the real products do

These are the vendor's own statements from public pages and press releases, not independently verified.

### Portrait Mobile (continuous, wearable)

- Wearable wireless sensors (a respiration rate sensor, and a pulse oximetry sensor in boot and wrap styles) send data to a smartphone-sized patient monitor that mounts on a pole or wall ([product page](https://www.gehealthcare.com/products/patient-monitoring/portrait-mobile/)).
- It measures three vital signs continuously: respiration rate (dual vector, with a motion-tolerant algorithm), SpO2 and pulse rate ([product page](https://www.gehealthcare.com/products/patient-monitoring/portrait-mobile/); [Business Wire, 2023](https://www.businesswire.com/news/home/20230814055987/en)).
- It gives "continuous trending and actionable alarms"; alarm limits and delays are configurable to clinical preferences, and alarms can be routed to a central viewer ([product page](https://www.gehealthcare.com/products/patient-monitoring/portrait-mobile/); [MedTech Dive](https://www.medtechdive.com/news/ge-healthcare-fda-clearance-wireless-vital-sign-monitor/690988/)).
- It uses the hospital's existing network and integrates with the EMR through IHE and HL7, including admitting and discharging patients ([product page](https://www.gehealthcare.com/products/patient-monitoring/portrait-mobile/)).
- FDA 510(k) clearance was announced in August 2023 ([Business Wire, 2023](https://www.businesswire.com/news/home/20230814055987/en)).

### Portrait VSM (spot checks)

- A portable vital signs monitor for spot checks: blood pressure, pulse rate, SpO2, temperature and respiratory rate ([Business Wire, 2024](https://www.businesswire.com/news/home/20240429837183/en/GE-HealthCare-Receives-FDA-Clearance-for-Portrait-VSM-Building-on-Its-Growing-Ecosystem-of-Connected-Patient-Monitoring-Solutions)).
- A barcode scan admits the patient and imports a snapshot of the continuous data from Portrait Mobile; Early Warning Score protocols can be built in, and a "Round Advisor" helps staff plan spot checks across patients (same source).

### CARESCAPE Central Station and alarm management (central view)

- A central station gives one view of current and past patient data, keeps a patient's data for up to six days after discharge, and keeps it available as patients move within or between care areas ([product page](https://www.gehealthcare.com/en/products/patient-monitoring/patient-monitors/carescape-central-station)).
- From the central station, users admit and discharge patients and manage their alarms ([alarm management](https://www.gehealthcare.com/en-in/products/patient-monitoring/alarm-management)).
- Alarm management distinguishes physiological alarms (heart rate, respiratory rate, SpO2, blood pressure) from technical alarms (lead detached, sensor failure); priorities can be adjusted within unit guidelines, absolute limits set by an administrator cannot be crossed, and some alarms are locked so they cannot be turned off (same source).
- After a wireless patient comes back into range, alarms reactivate only once continuous data has been received for a while, 15 seconds in the telemetry example (same source).

## Clinical guidance used for the model

- IEC 60601-1-8, the standard for alarm systems in medical electrical equipment, separates physiological alarms (patient condition) from technical alarms (equipment status) and classifies alarms as low, medium or high priority ([Same Sky](https://www.sameskydevices.com/blog/a-guide-to-iec-60601-1-8-and-medical-alarm-systems)).
- NEWS2 (Royal College of Physicians) scores six parameters, three of which PulseGuard measures: respiration rate, SpO2 and pulse rate ([RCP](https://www.rcp.ac.uk/resources/national-early-warning-score-news-2/)).
- NEWS2 triggers: an aggregate of 5 or more is an urgent response threshold, 7 or more an emergency threshold with continuous monitoring, and 3 in a single parameter prompts a review ([RCN crib card](https://preprodstartingout.rcn.org.uk/Develop/Nursing-Essentials-Crib-Cards/NEWS2)).
- The scoring bands found (respiration 12 to 20, SpO2 96 % and above, pulse 51 to 90 score 0) come from a page with typing errors in its top bands, so every threshold must be checked against the RCP chart before it becomes a requirement.

## The ward workflow PulseGuard models

| Step | What happens | Who | What can go wrong (test ideas) |
|---|---|---|---|
| Admission | A patient is admitted to a ward bed and a monitoring session opens | Nurse | Two open sessions for one patient; admission to a full or unknown unit |
| Pairing | Sensors are attached and paired with the patient's monitor, which is tied to the session | Nurse, technician | A sensor paired to the wrong patient; a sensor still paired to a discharged patient |
| Monitoring | Sensors send RR, SpO2 and pulse continuously; the monitor forwards them to the central system | System | Data from one patient shown for another; duplicate or out-of-order readings; stale data shown as current |
| Alarm | A value outside its limits, or a lost sensor, raises an alarm with a priority; staff acknowledge it and it is resolved when the value returns or the cause is fixed | System, nurse | A missed alarm; a flood of alarms from one noisy value (no debounce); an alarm that resolves itself while unacknowledged; a technical alarm hidden by a physiological one |
| Discharge | The session closes, sensors are unpaired, data is kept for review | Nurse | Readings still accepted after discharge; alarms left open on a closed session |
| Reassignment | Sensors and monitors are cleaned and assigned to the next patient; a patient may move to another bed or unit | Technician, nurse | The new patient sees the old patient's history; a device in two sessions at once |

## What PulseGuard models and what it leaves out

Modelled:

- Three vital signs: respiration rate, SpO2 and pulse (heart) rate, from seeded wearable sensor simulators.
- A patient monitor that receives sensor data over a TCP protocol and forwards it to a backend.
- Sessions, devices and their lifecycle, measurements, alarms with priority and lifecycle, technical alarms for lost connections.
- A central view for nurses, with roles for nurse, physician, technician and admin.

Left out on purpose:

- ECG, blood pressure, temperature and consciousness, so no full NEWS2 score.
- Integration with an EMR, HL7 or IHE.
- Real radio protocols, real devices and any real patient data.

## Questions for the requirements

- Which alarm limits and priorities apply by default, and may a nurse change them per patient within absolute limits?
- How long must a value stay out of range before an alarm is raised (debounce), and how long must it be back in range before it resolves?
- Does an alarm resolve by itself when the value returns, or does it stay until acknowledged (latching)?
- After how long without data is a sensor treated as disconnected, and which priority has that technical alarm?
- How long is a session's data kept after discharge, and who may see it?
