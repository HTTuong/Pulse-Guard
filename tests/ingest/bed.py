"""A bed with a patient monitor and two sensors, and helpers to send batches as that monitor."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from httpx import Response

from pulseguard.clock import to_wire
from tests.ward import NURSE_W1, TECHNICIAN, Ward


@dataclass
class Bed:
    ward: Ward
    session: str
    monitor_serial: str
    credential: str
    device_ids: dict[str, str] = field(default_factory=dict)

    def send(self, readings: list[dict[str, object]], **extra: object) -> Response:
        body = {
            "monitor": self.monitor_serial,
            "sent_at": to_wire(self.ward.clock.now()),
            "readings": readings,
        } | extra
        response: Response = self.ward.client(None).post(
            "/api/v1/ingest/batches",
            json=body,
            headers={"Authorization": f"Bearer {self.credential}"},
        )
        return response


def register_monitor(ward: Ward, serial: str) -> tuple[str, str]:
    response = ward.client(TECHNICIAN).post(
        "/api/v1/devices", json={"serial": serial, "kind": "PATIENT_MONITOR"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"]), str(response.json()["credential"])


def assign(ward: Ward, session: str, device: str) -> None:
    response = ward.client(NURSE_W1).post(
        f"/api/v1/sessions/{session}/devices", json={"device_id": device}
    )
    assert response.status_code == 201, response.text


def equipped_bed(ward: Ward, number: int, sensors: bool = True) -> Bed:
    """Patient `number` in bed W1-B`number` with monitor SN-PM-000n and, when asked, a pulse
    oximeter SN-PO-000n and a respiration sensor SN-RR-000n, all assigned at the ward clock."""
    session = ward.open_session(number, number)
    monitor_id, credential = register_monitor(ward, f"SN-PM-{number:04d}")
    bed = Bed(ward, session, f"SN-PM-{number:04d}", credential, {f"SN-PM-{number:04d}": monitor_id})
    assign(ward, session, monitor_id)
    if sensors:
        for serial, kind in [
            (f"SN-PO-{number:04d}", "PULSE_OXIMETER"),
            (f"SN-RR-{number:04d}", "RESPIRATION"),
        ]:
            device = ward.new_device(serial, kind)
            assign(ward, session, device)
            bed.device_ids[serial] = device
    return bed


def reading(
    device: str,
    seq: int,
    at: datetime,
    parameter: str = "PR",
    value: object = 72,
    unit: str | None = None,
) -> dict[str, object]:
    units = {"PR": "beats/min", "SPO2": "%", "RR": "breaths/min"}
    return {
        "device": device,
        "seq": seq,
        "parameter": parameter,
        "value": value,
        "unit": unit or units.get(parameter, "?"),
        "measured_at": to_wire(at),
    }


def later(ward: Ward, seconds: float) -> datetime:
    return ward.clock.now() + timedelta(seconds=seconds)


class Pulse:
    """Pulse readings one per second from the ward clock, with their own seq numbers."""

    def __init__(self, bed: Bed, sensor: str = "SN-PO-0001") -> None:
        self.bed, self.seq, self.sensor = bed, 0, sensor
        self.start = bed.ward.clock.now() - timedelta(seconds=40)
        self.second = 0

    def readings(self, values: Sequence[float], parameter: str = "PR") -> list[dict[str, object]]:
        batch = []
        for value in values:
            self.seq += 1
            at = self.start + timedelta(seconds=self.second)
            batch.append(reading(self.sensor, self.seq, at, parameter, value))
            self.second += 1
        return batch

    def send(self, values: Sequence[float], parameter: str = "PR") -> None:
        response = self.bed.send(self.readings(values, parameter))
        assert response.status_code == 200, response.text
        assert len(response.json()["stored"]) == len(values), response.json()

    def at(self, second: int) -> datetime:
        return self.start + timedelta(seconds=second)
