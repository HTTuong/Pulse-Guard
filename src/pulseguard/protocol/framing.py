"""Frames: a 4 byte big-endian length, then that many bytes (P-01, P-02, P-03).

`FrameDecoder` does no I/O. It is given the bytes that arrived and returns the complete frames,
so the same code is tested with bytes in a list and used with a socket.
"""

import json
import struct
from dataclasses import dataclass, field
from typing import Any

VERSION = 1
MAX_FRAME = 4096  # P-02


def encode_frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 1 <= len(body) <= MAX_FRAME:
        raise ValueError(f"frame body of {len(body)} bytes; P-02 allows 1 to {MAX_FRAME}")
    return struct.pack(">I", len(body)) + body


class FramingError(Exception):
    """The length of a frame is not allowed (P-02): the next frame can no longer be located."""


@dataclass
class FrameDecoder:
    _buffer: bytearray = field(default_factory=bytearray)

    @property
    def pending(self) -> bool:
        """True while bytes of an incomplete frame are held back (P-03)."""
        return bool(self._buffer)

    def feed(self, data: bytes) -> tuple[list[bytes], FramingError | None]:
        """Complete frames found so far, and the error that stopped decoding, if any.

        Frames before a bad length are still returned: they arrived intact and were valid.
        """
        self._buffer += data
        frames: list[bytes] = []
        while len(self._buffer) >= 4:
            (length,) = struct.unpack(">I", self._buffer[:4])
            if not 1 <= length <= MAX_FRAME:
                self._buffer.clear()
                return frames, FramingError(f"frame length {length}, allowed 1 to {MAX_FRAME}")
            if len(self._buffer) < 4 + length:
                break
            frames.append(bytes(self._buffer[4 : 4 + length]))
            del self._buffer[: 4 + length]
        return frames, None
