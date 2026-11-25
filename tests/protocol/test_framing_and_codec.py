"""Framing (P-01 to P-03) and the strict JSON reader (P-04, P-05), without any socket."""

import struct

import pytest

from pulseguard.protocol.codec import BadJson, decode_object, parse_wire_time
from pulseguard.protocol.framing import MAX_FRAME, FrameDecoder, encode_frame


def frame(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body


# ---- framing -------------------------------------------------------------------------------


def test_one_frame_in_one_read() -> None:
    frames, error = FrameDecoder().feed(frame(b'{"a":1}'))
    assert frames == [b'{"a":1}'] and error is None


def test_several_frames_in_one_read() -> None:
    frames, _ = FrameDecoder().feed(frame(b"{}") + frame(b'{"a":1}') + frame(b'{"b":2}'))
    assert frames == [b"{}", b'{"a":1}', b'{"b":2}']


def test_a_frame_split_at_every_possible_place() -> None:
    data = frame(b'{"a":1}') + frame(b"{}")
    for cut in range(len(data) + 1):
        decoder = FrameDecoder()
        first, e1 = decoder.feed(data[:cut])
        second, e2 = decoder.feed(data[cut:])
        assert first + second == [b'{"a":1}', b"{}"] and e1 is None and e2 is None, cut
        assert not decoder.pending


def test_one_byte_at_a_time() -> None:
    decoder = FrameDecoder()
    out: list[bytes] = []
    for byte in frame(b'{"a":1}'):
        frames, _ = decoder.feed(bytes([byte]))
        out += frames
    assert out == [b'{"a":1}']


def test_pending_is_true_while_a_frame_is_incomplete() -> None:
    decoder = FrameDecoder()
    decoder.feed(frame(b'{"a":1}')[:3])
    assert decoder.pending
    decoder.feed(frame(b'{"a":1}')[3:])
    assert not decoder.pending


@pytest.mark.parametrize("length", [0, MAX_FRAME + 1, 2**32 - 1])
def test_a_length_outside_1_to_4096_is_an_error_at_once(length: int) -> None:
    # Only the 4 header bytes arrive: waiting for a 4 GB body would be the attack.
    frames, error = FrameDecoder().feed(struct.pack(">I", length))
    assert frames == [] and error is not None


@pytest.mark.parametrize("length", [1, MAX_FRAME])
def test_the_limits_themselves_are_allowed(length: int) -> None:
    frames, error = FrameDecoder().feed(frame(b" " * length))
    assert len(frames) == 1 and error is None


def test_frames_before_a_bad_length_are_not_lost() -> None:
    frames, error = FrameDecoder().feed(frame(b"{}") + struct.pack(">I", 0))
    assert frames == [b"{}"] and error is not None


def test_encode_refuses_what_decode_would() -> None:
    with pytest.raises(ValueError, match="P-02"):
        encode_frame({"pad": "x" * (MAX_FRAME + 1)})
    assert len(encode_frame({"a": 1})) == 4 + len(b'{"a":1}')


# ---- strict JSON ---------------------------------------------------------------------------


def test_a_plain_object_is_read() -> None:
    assert decode_object(b'{"v":1,"type":"HEARTBEAT"}') == {"v": 1, "type": "HEARTBEAT"}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b'{"a":',
        b"[1,2]",
        b'"text"',
        b"42",
        b"null",
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"a":1e999}',
        b'{"a":-1e999}',
        b'{"a":1,"a":2}',
        b'{"a":{"b":1,"b":1}}',
        b"\xff\xfe{}",
        b'\xef\xbb\xbf{"a":1}',  # a byte order mark is not JSON
    ],
)
def test_bad_json_is_refused(body: bytes) -> None:
    with pytest.raises(BadJson):
        decode_object(body)


def test_the_deepest_nesting_that_fits_a_frame_does_not_crash_the_reader() -> None:
    # Measured on 2026-11-25 (Python 3.12.3): json.loads nests 2048 levels without a
    # RecursionError, and a frame of 4096 bytes holds at most 2047 levels. So a frame cannot
    # overflow the parser; what it contains is judged by the field checks, not by a crash.
    body = b'{"a":' + b"[" * 2040 + b"]" * 2040 + b"}"
    assert len(body) <= MAX_FRAME
    assert "a" in decode_object(body)


def test_a_recursion_error_inside_the_parser_is_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Should a newer Python lower its limit, a deep frame is still BadJson and not a dead handler.
    def too_deep(*args: object, **kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr("pulseguard.protocol.codec.json.loads", too_deep)
    with pytest.raises(BadJson, match="deep"):
        decode_object(b"{}")


def test_python_json_alone_would_accept_what_we_refuse() -> None:
    # The reason decode_object exists: these all pass json.loads (the finding of P-04).
    import json

    assert json.loads('{"a":NaN}')["a"] != json.loads('{"a":NaN}')["a"]
    assert json.loads('{"a":1,"a":2}') == {"a": 2}
    assert json.loads('{"a":1e999}') == {"a": float("inf")}


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-12T18:52:03.000Z",
        "2026-02-28T23:59:59.999Z",
        "2028-02-29T00:00:00.000Z",
    ],
)
def test_wire_times_are_read(text: str) -> None:
    parsed = parse_wire_time(text)
    assert parsed is not None and parsed.utcoffset() is not None


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-12T18:52:03Z",  # no milliseconds
        "2026-11-12T18:52:03.000",  # no offset
        "2026-11-12T18:52:03.000+00:00",  # another spelling of UTC
        "2026-11-12T20:52:03.000+02:00",  # another offset
        "2026-11-12 18:52:03.000Z",
        "2026-11-12T18:52:03.0000Z",
        "2026-02-30T18:52:03.000Z",  # no such day
        "2027-02-29T00:00:00.000Z",
        "2026-11-12T24:00:00.000Z",
        "2026-11-12T18:52:60.000Z",
        "٢٠٢٦-١١-١٢T18:52:03.000Z",  # digits of another script
        "2026-11-12T18:52:03.000Z\n",
        "",
    ],
)
def test_other_times_are_refused(text: str) -> None:
    assert parse_wire_time(text) is None


@pytest.mark.parametrize("value", [None, 5, 1.5, ["2026-11-12T18:52:03.000Z"]])
def test_a_time_that_is_not_text_is_refused(value: object) -> None:
    assert parse_wire_time(value) is None
