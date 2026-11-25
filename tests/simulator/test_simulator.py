"""The sensor simulator: the same seed gives the same readings, scenarios do what they name,
and what it sends passes the rules the backend applies (measurements/rules.py)."""

import json
import struct
from datetime import UTC, datetime, timedelta

import pytest

from pulseguard.alarms.limits import DEFAULT_LIMITS
from pulseguard.measurements import rules
from pulseguard.simulator import wire
from pulseguard.simulator.generator import SensorSimulator, Tick
from pulseguard.simulator.scenarios import Keyframe, Scenario, scenario_named

START = datetime(2026, 11, 12, 18, 52, 0, tzinfo=UTC)


def simulate(
    scenario: str = "normal",
    seconds: int = 120,
    seed: int = 1,
    device: str = "SN-PO-0001",
    device_type: str = "PULSE_OXIMETER",
) -> list[Tick]:
    sim = SensorSimulator(device, device_type, scenario_named(scenario), seed, START)
    return list(sim.run(seconds))


def values(ticks: list[Tick], parameter: str) -> list[int]:
    return [r.value for t in ticks for r in t.readings if r.parameter == parameter]


# ---- repeatability: the first task of d247 -------------------------------------------------


def test_same_seed_gives_the_same_readings() -> None:
    assert simulate(seed=7) == simulate(seed=7)


def test_another_seed_gives_other_readings() -> None:
    assert values(simulate(seed=7), "PR") != values(simulate(seed=8), "PR")


def test_another_device_has_its_own_noise_with_the_same_seed() -> None:
    assert values(simulate(device="SN-PO-0001"), "PR") != values(
        simulate(device="SN-PO-0002"), "PR"
    )


def test_the_readings_do_not_depend_on_the_system_clock_or_process() -> None:
    # A fixed seed gives fixed numbers across runs and machines; these were taken from a real
    # run and would change if the seeding became process dependent (hash() of a string does).
    assert values(simulate(seed=1, seconds=5), "PR") == [75, 72, 75, 74, 73]


def test_pr_does_not_change_when_only_one_parameter_is_asked_for() -> None:
    both = simulate(device_type="PULSE_OXIMETER")
    again = simulate(device_type="PULSE_OXIMETER")
    resp = simulate(device_type="RESPIRATION", device="SN-RR-0001")
    assert values(both, "PR") == values(again, "PR")
    assert values(resp, "PR") == []


# ---- sequence numbers and shape of a reading (P-14) ----------------------------------------


def test_seq_starts_at_1_and_goes_up_by_1_across_parameters() -> None:
    seqs = [r.seq for t in simulate(seconds=10) for r in t.readings]
    assert seqs == list(range(1, 21))


def test_first_seq_is_kept_so_a_restarted_sensor_never_reuses_a_number() -> None:
    first = SensorSimulator("SN-PO-0001", "PULSE_OXIMETER", scenario_named("normal"), 1, START)
    list(first.run(5))
    second = SensorSimulator(
        "SN-PO-0001",
        "PULSE_OXIMETER",
        scenario_named("normal"),
        1,
        START,
        first_seq=first.last_seq + 1,
    )
    assert second.tick().readings[0].seq == 11


def test_a_respiration_sensor_sends_rr_only() -> None:
    ticks = simulate(device_type="RESPIRATION", device="SN-RR-0001", seconds=3)
    assert [r.parameter for t in ticks for r in t.readings] == ["RR", "RR", "RR"]


def test_one_reading_per_second_at_whole_seconds() -> None:
    ticks = simulate(seconds=3)
    assert [t.at for t in ticks] == [START + timedelta(seconds=n) for n in range(3)]
    assert all(r.measured_at == t.at for t in ticks for r in t.readings)


# ---- scenarios: the second task of d247 ----------------------------------------------------


def test_normal_stays_in_the_normal_band_for_an_hour() -> None:
    ticks = simulate("normal", seconds=3600)
    assert all(51 <= v <= 90 for v in values(ticks, "PR"))
    assert all(94 <= v <= 100 for v in values(ticks, "SPO2"))
    assert all(
        9 <= v <= 20 for v in values(simulate("normal", 3600, device_type="RESPIRATION"), "RR")
    )


def test_tachycardia_ends_above_the_default_high_limit_and_spo2_stays_normal() -> None:
    ticks = simulate("tachycardia", seconds=120)
    pr = values(ticks, "PR")
    assert max(pr[:30]) < 90  # the first 30 s are normal
    high = DEFAULT_LIMITS["PR"].high_high
    assert high is not None
    assert all(v >= high for v in pr[-30:])  # last 30 s
    assert all(v >= 94 for v in values(ticks, "SPO2"))


def test_desaturation_ends_below_the_default_high_limit_and_pr_stays_normal() -> None:
    ticks = simulate("desaturation", seconds=120)
    spo2 = values(ticks, "SPO2")
    assert min(spo2[:30]) >= 94
    high_low = DEFAULT_LIMITS["SPO2"].high_low
    assert high_low is not None
    assert all(v <= high_low for v in spo2[-30:])
    assert all(v <= 90 for v in values(ticks, "PR"))


def test_sensor_off_sends_nothing_for_30_s_and_then_continues_without_a_seq_gap() -> None:
    ticks = simulate("sensor_off", seconds=90)
    off = [t for t in ticks if not t.signal]
    assert [t.at for t in off] == [START + timedelta(seconds=n) for n in range(30, 60)]
    assert all(t.readings == () for t in off)
    seqs = [r.seq for t in ticks for r in t.readings]
    assert seqs == list(range(1, len(seqs) + 1))  # an off probe takes no seq: nothing was lost


def test_sensor_off_is_longer_than_the_15_s_of_sensor_lost() -> None:
    gap = max(end - start for start, end in scenario_named("sensor_off").no_signal)
    assert gap > 15


def test_values_follow_the_ramp_between_keyframes() -> None:
    scenario = Scenario("ramp", (Keyframe(0, 60, 97, 16), Keyframe(10, 100, 97, 16)))
    assert scenario.target(5)[0] == pytest.approx(80)
    assert scenario.target(10)[0] == 100
    assert scenario.target(500)[0] == 100


@pytest.mark.parametrize(
    "frames",
    [(), (Keyframe(5, 1, 1, 1),), (Keyframe(0, 1, 1, 1), Keyframe(0, 2, 2, 2))],
)
def test_a_scenario_with_bad_keyframes_is_refused(frames: tuple[Keyframe, ...]) -> None:
    with pytest.raises(ValueError, match="keyframes"):
        Scenario("bad", frames)


def test_an_unknown_scenario_names_the_known_ones() -> None:
    with pytest.raises(ValueError, match="normal"):
        scenario_named("fever")


def test_noise_never_takes_a_value_outside_the_plausible_range() -> None:
    extreme = Scenario("extreme", (Keyframe(0, 299, 100, 119),))
    sim = SensorSimulator("SN-PO-0001", "PULSE_OXIMETER", extreme, 3, START)
    ticks = list(sim.run(3000))
    assert max(values(ticks, "PR")) <= 300
    assert max(values(ticks, "SPO2")) <= 100


# ---- what it sends passes the backend's own rules ------------------------------------------


@pytest.mark.parametrize("name", ["normal", "tachycardia", "desaturation", "sensor_off"])
def test_every_reading_passes_the_ingest_rules(name: str) -> None:
    for device_type, device in (("PULSE_OXIMETER", "SN-PO-0001"), ("RESPIRATION", "SN-RR-0001")):
        for tick in simulate(name, 120, device=device, device_type=device_type):
            for r in tick.readings:
                assert rules.check_kind(device_type, r.parameter) is None
                assert rules.check_unit(r.parameter, r.unit) is None
                assert rules.check_time(r.measured_at, tick.at) is None
                assert rules.check_value(r.parameter, rules.stored_value(r.value)) is None


# ---- the wire format (P-01, P-02, P-05) ----------------------------------------------------


def decode(frame: bytes) -> dict[str, object]:
    (length,) = struct.unpack(">I", frame[:4])
    assert length == len(frame) - 4
    body = json.loads(frame[4:])
    assert isinstance(body, dict)
    return body


def test_the_example_of_protocol_md_is_reproduced_byte_for_byte() -> None:
    reading = next(
        r
        for t in SensorSimulator(
            "SN-PO-0001", "PULSE_OXIMETER", scenario_named("normal"), 1, START
        ).run(1)
        for r in t.readings
    )
    # Same fields as the example in protocol.md; the numbers of that example are set by hand.
    sample = type(reading)(41, "PR", 72, "beats/min", datetime(2026, 11, 12, 18, 52, 3, tzinfo=UTC))
    frame = wire.encode_frame(
        wire.measurement("SN-PO-0001", sample, datetime(2026, 11, 12, 18, 52, 3, 20000, tzinfo=UTC))
    )
    assert frame[:4] == bytes.fromhex("000000b8")
    assert frame[4:].decode() == (
        '{"v":1,"type":"MEASUREMENT","device":"SN-PO-0001","seq":41,"parameter":"PR","value":72,'
        '"unit":"beats/min","measured_at":"2026-11-12T18:52:03.000Z","sent_at":"2026-11-12T18:52:03.020Z"}'
    )


def test_times_have_milliseconds_and_a_z() -> None:
    message = wire.heartbeat("NO_SIGNAL", START)
    assert message["sent_at"] == "2026-11-12T18:52:00.000Z"
    assert decode(wire.encode_frame(message))["status"] == "NO_SIGNAL"


def test_hello_lists_the_version_and_the_parameters_of_the_device() -> None:
    sim = SensorSimulator("SN-PO-0001", "PULSE_OXIMETER", scenario_named("normal"), 1, START)
    hello = decode(
        wire.encode_frame(wire.hello(sim.device, sim.device_type, sim.parameters, START))
    )
    assert hello["versions"] == [1]
    assert hello["parameters"] == ["PR", "SPO2"]


def test_a_frame_above_4096_bytes_is_not_built() -> None:
    with pytest.raises(ValueError, match="P-02"):
        wire.encode_frame({"v": 1, "type": "HEARTBEAT", "pad": "x" * 5000})


def test_nan_is_never_put_on_the_wire() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        wire.encode_frame({"v": 1, "type": "MEASUREMENT", "value": float("nan")})
