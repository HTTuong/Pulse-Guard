"""The sensor's outbox (P-15 to P-17, P-20) and backoff (P-19), as plain functions."""

from datetime import UTC, datetime, timedelta

import pytest

from pulseguard.simulator.backoff import Backoff
from pulseguard.simulator.generator import Reading
from pulseguard.simulator.outbox import Outbox

T0 = datetime(2026, 11, 12, 18, 52, 0, tzinfo=UTC)


def reading(seq: int) -> Reading:
    return Reading(seq, "PR", 72, "beats/min", T0 + timedelta(seconds=seq))


def filled(count: int, **kwargs: int | float) -> Outbox:
    box = Outbox(**kwargs)  # type: ignore[arg-type]
    for seq in range(1, count + 1):
        box.add(reading(seq))
    return box


def seqs(readings: list[Reading]) -> list[int]:
    return [r.seq for r in readings]


# ---- sending and the window (P-16) ---------------------------------------------------------


def test_new_readings_are_sent_in_seq_order() -> None:
    assert seqs(filled(5).due(now=0)) == [1, 2, 3, 4, 5]


def test_at_most_50_readings_wait_for_an_answer_and_the_rest_queue_in_order() -> None:
    box = filled(80)
    assert seqs(box.due(0)) == list(range(1, 51))
    assert box.waiting == 50 and len(box) == 80
    assert box.due(0.1) == []  # window full, nothing overdue


def test_an_answer_opens_the_window_for_the_next_reading_in_seq_order() -> None:
    box = filled(80)
    box.due(0)
    for seq in (3, 1, 2):
        assert box.answered(seq)
    assert seqs(box.due(0.1)) == [51, 52, 53]


def test_a_reading_is_not_sent_again_before_the_answer_time_is_over() -> None:
    box = filled(2)
    box.due(0)
    assert box.due(2.999) == []
    assert seqs(box.due(3.0)) == [1, 2]  # P-16: no answer within 3 s


def test_an_overdue_reading_is_sent_again_with_the_same_seq_and_content() -> None:
    box = filled(1)
    (first,) = box.due(0)
    (again,) = box.due(3.0)
    assert again == first and box.resent == 1
    assert box.due(5.9) == []  # the wait starts again from the new send
    assert len(box.due(6.0)) == 1 and box.resent == 2


def test_overdue_readings_go_before_new_ones() -> None:
    box = filled(2, window=2)
    box.due(0)
    box.add(reading(3))
    assert seqs(box.due(3.0)) == [1, 2]  # window is full of waiting ones; 3 has to wait
    box.answered(1)
    assert seqs(box.due(3.1)) == [3]


# ---- answers (P-15) ------------------------------------------------------------------------


def test_a_reading_stays_until_answered() -> None:
    box = filled(3)
    box.due(0)
    for t in (3, 6, 9, 12):
        assert len(box) == 3
        box.due(t)
    assert box.answered(2) and seqs([reading(s) for s in box.seqs()]) == [1, 3]


def test_an_answer_for_a_reading_not_held_is_ignored() -> None:
    box = filled(2)
    assert not box.answered(99)
    assert box.answered(1) and not box.answered(1)  # a repeated ACK
    assert box.seqs() == [2]


def test_next_deadline_is_when_the_oldest_waiting_reading_is_overdue() -> None:
    box = filled(3)
    assert box.next_deadline() is None  # nothing waiting yet
    box.due(10)
    assert box.next_deadline() == 13
    box.answered(1)
    box.answered(2)
    box.answered(3)
    assert box.next_deadline() is None


# ---- reconnection (P-20) -------------------------------------------------------------------


def test_after_a_lost_connection_every_waiting_reading_goes_at_once_oldest_first() -> None:
    box = filled(60)
    box.due(0)  # 50 waiting, 10 unsent
    box.answered(4)
    box.connection_lost()
    # 49 waiting readings go first, then one new one: answering 4 freed exactly one place of 50.
    assert seqs(box.due(0.5)) == [s for s in range(1, 51) if s != 4] + [51]
    assert box.waiting == 50


def test_resends_after_reconnect_come_before_new_readings_even_when_new_ones_arrived_offline() -> (
    None
):
    box = filled(3)
    box.due(0)
    box.connection_lost()
    box.add(reading(4))
    assert seqs(box.due(1)) == [1, 2, 3, 4]


def test_a_connection_lost_with_nothing_waiting_changes_nothing() -> None:
    box = filled(2)
    box.connection_lost()
    assert seqs(box.due(0)) == [1, 2]
    assert box.resent == 0


# ---- the limit (P-17) ----------------------------------------------------------------------


def test_beyond_1200_readings_the_oldest_is_dropped_and_counted() -> None:
    box = filled(1200)
    assert box.dropped == 0
    box.add(reading(1201))
    box.add(reading(1202))
    assert box.dropped == 2 and len(box) == 1200 and box.seqs()[0] == 3 and box.seqs()[-1] == 1202


def test_a_dropped_reading_leaves_a_gap_in_the_seqs_that_are_sent() -> None:
    box = filled(5, limit=3)
    assert seqs(box.due(0)) == [3, 4, 5]  # 1 and 2 are gone; the monitor will see the gap (P-14)


def test_the_default_limit_is_ten_minutes_of_a_pulse_oximeter() -> None:
    assert Outbox().limit == 1200 and Outbox().window == 50 and Outbox().ack_s == 3.0


def test_a_seq_is_never_reused_or_taken_back() -> None:
    box = filled(3)
    for bad in (3, 2, 1):
        with pytest.raises(ValueError, match="never reused"):
            box.add(reading(bad))


# ---- unpairing (P-10) ----------------------------------------------------------------------


def test_readings_measured_at_or_after_the_unassignment_are_dropped_the_rest_are_kept() -> None:
    box = filled(5)
    removed = box.discard_measured_from(T0 + timedelta(seconds=3))  # reading n is at T0 + n s
    assert removed == 3 and box.seqs() == [1, 2]


# ---- backoff (P-19) ------------------------------------------------------------------------


def delays(backoff: Backoff, count: int) -> list[float]:
    return [backoff.next_delay() for _ in range(count)]


def test_without_jitter_the_waits_are_1_2_4_8_16_then_30_for_ever() -> None:
    assert delays(Backoff(1, "SN", jitter=0), 9) == [1, 2, 4, 8, 16, 30, 30, 30, 30]


@pytest.mark.parametrize("seed", range(20))
def test_every_wait_is_within_10_percent_of_its_step(seed: int) -> None:
    for got, base in zip(delays(Backoff(seed, "SN"), 12), [1, 2, 4, 8, 16] + [30] * 7, strict=True):
        assert 0.9 * base <= got <= 1.1 * base


def test_the_same_seed_and_device_give_the_same_waits() -> None:
    assert delays(Backoff(7, "SN-1"), 10) == delays(Backoff(7, "SN-1"), 10)


def test_another_seed_or_another_device_gives_other_jitter() -> None:
    base = delays(Backoff(7, "SN-1"), 10)
    assert base != delays(Backoff(8, "SN-1"), 10)
    assert base != delays(Backoff(7, "SN-2"), 10)


def test_jitter_actually_varies() -> None:
    waits = delays(Backoff(3, "SN"), 40)[5:]  # all at the 30 s step
    assert len({round(w, 6) for w in waits}) > 30 and min(waits) < 30 < max(waits)


def test_reset_starts_at_1_s_again_but_the_jitter_goes_on() -> None:
    plain = Backoff(5, "SN")
    first_round = delays(plain, 3)
    plain.reset()
    second_round = delays(plain, 3)
    assert 0.9 <= second_round[0] <= 1.1  # back to the 1 s step
    assert second_round != first_round  # not the same three numbers: the generator was not reset


def test_scale_shrinks_every_wait_by_the_same_factor() -> None:
    full, small = delays(Backoff(9, "SN"), 8), delays(Backoff(9, "SN", scale=0.01), 8)
    assert small == pytest.approx([w * 0.01 for w in full])


def test_the_seeding_does_not_depend_on_the_process() -> None:
    # Taken from a real run; changes if the seed were mixed with hash() of a string.
    assert delays(Backoff(1, "SN-PO-0001"), 3) == pytest.approx(
        [0.995961947926441, 1.9852598581144936, 4.204336648868232]
    )
