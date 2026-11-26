"""Unpairing while connected (P-10): UNPAIRED, old readings accepted, new ones refused."""

import asyncio
from datetime import timedelta

from pulseguard.clock import to_wire
from tests.monitor.helpers import DEVICE, NOW, PAIRING, Rig, at, code_of, measurement, run
from tests.monitor.test_server import Rig as ServerRig
from tests.monitor.test_server import scenario

UNPAIR_AT = NOW - timedelta(seconds=10)


def connected_then_unpaired() -> tuple[Rig, list[dict[str, object]]]:
    rig = Rig()
    rig.connect()
    rig.pairing.unpair(DEVICE, UNPAIR_AT)
    return rig, list(run(rig.session.check_pairing()))


def test_unassigning_gives_one_unpaired_frame_with_the_time() -> None:
    rig, frames = connected_then_unpaired()
    assert [(f["type"], f["status"], f["unpaired_at"]) for f in frames] == [
        ("PAIR", "UNPAIRED", to_wire(UNPAIR_AT))
    ]
    assert not rig.session.paired
    assert run(rig.session.check_pairing()) == []  # said once
    # and no NOT_PAIRED on top of it, however often the backend is asked
    assert run(rig.session.check_pairing()) == []


def test_when_the_backend_gives_no_time_the_moment_it_was_noticed_is_used() -> None:
    rig = Rig()
    rig.connect()
    del rig.pairing.pairs[DEVICE]  # the backend now knows nothing of this sensor
    (frame,) = run(rig.session.check_pairing())
    assert (frame["status"], frame["unpaired_at"]) == ("UNPAIRED", to_wire(NOW))


def test_a_reading_measured_before_the_unassignment_is_still_accepted() -> None:
    rig, _ = connected_then_unpaired()
    reply = rig.send(measurement(seq=7, measured_at=to_wire(UNPAIR_AT - timedelta(milliseconds=1))))
    assert reply.messages[0]["status"] == "STORED"


def test_a_reading_measured_at_or_after_the_unassignment_is_refused_not_paired() -> None:
    rig, _ = connected_then_unpaired()
    for n, offset in enumerate((0, 1, 30), start=1):  # the time itself: ranges are half open
        reply = rig.send(
            measurement(seq=n, measured_at=to_wire(UNPAIR_AT + timedelta(seconds=offset)))
        )
        assert code_of(reply) == "NOT_PAIRED" and reply.messages[0]["seq"] == n
    assert rig.buffer.readings == {}


def test_pairing_again_lets_new_readings_through_with_the_new_id() -> None:
    rig, _ = connected_then_unpaired()
    rig.pairing.pair(DEVICE, "pr-9999")
    (frame,) = run(rig.session.check_pairing())
    assert (frame["status"], frame["pairing"]) == ("PAIRED", "pr-9999")
    assert rig.send(measurement(seq=1)).messages[0]["status"] == "STORED"


def test_a_new_pairing_id_while_paired_is_announced() -> None:
    rig = Rig()
    rig.connect()
    rig.pairing.pair(DEVICE, "pr-2")
    (frame,) = run(rig.session.check_pairing())
    assert (frame["status"], frame["pairing"]) == ("PAIRED", "pr-2")
    assert run(rig.session.check_pairing()) == []


def test_pinned_after_pairing_again_a_reading_from_the_gap_is_accepted_by_the_monitor() -> None:
    # PINNED: the monitor only knows the latest state. A reading measured while the sensor was
    # unpaired (between UNPAIR_AT and the new pairing) passes here; the backend refuses it
    # because no session covered that time (REQ-MEA-06, R-ISO-01). Two layers, as in the design.
    rig, _ = connected_then_unpaired()
    rig.pairing.pair(DEVICE, "pr-2")
    run(rig.session.check_pairing())
    gap = to_wire(UNPAIR_AT + timedelta(seconds=5))
    assert rig.send(measurement(seq=1, measured_at=gap)).messages[0]["status"] == "STORED"


def test_over_tcp_the_sensor_is_told_and_old_readings_still_go_through() -> None:
    async def test(rig: ServerRig) -> None:
        async with rig.client() as c:
            await c.connect_and_pair()
            rig.server.pairing.unpair(DEVICE, UNPAIR_AT)  # type: ignore[attr-defined]
            frame = await c.recv_type("PAIR")
            assert (frame["status"], frame["unpaired_at"]) == ("UNPAIRED", to_wire(UNPAIR_AT))
            await c.send({"v": 1, "type": "ACK", "ack": "PAIR", "sent_at": at()})
            await c.send(measurement(seq=1, measured_at=to_wire(UNPAIR_AT - timedelta(seconds=1))))
            assert (await c.recv_type("ACK"))["status"] == "STORED"
            await c.send(measurement(seq=2, measured_at=to_wire(UNPAIR_AT + timedelta(seconds=1))))
            assert (await c.recv_type("ERROR"))["code"] == "NOT_PAIRED"
            await asyncio.sleep(0.3)  # several more checks: no repeat of UNPAIRED, no NOT_PAIRED
            assert not any(m["type"] == "PAIR" for m in c.queue)

    scenario(test)


def test_the_pairing_table_pairs_and_unpairs() -> None:
    from pulseguard.monitor.pairing import Pairing, StaticPairing

    table = StaticPairing({DEVICE: PAIRING})
    assert run(table.pairing_of(DEVICE, "m")) == Pairing(PAIRING)
    table.unpair(DEVICE, UNPAIR_AT)
    assert run(table.pairing_of(DEVICE, "m")) == Pairing(PAIRING, UNPAIR_AT)
    table.pair(DEVICE, "x")
    assert run(table.pairing_of(DEVICE, "m")) == Pairing("x")
    assert run(table.pairing_of("other", "m")) is None
