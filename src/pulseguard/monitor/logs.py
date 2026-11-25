"""Structured logs: one JSON object per line, with an `event` name and fields.

A log line is read by a person and searched by a tool, so it has a fixed shape. What a sensor
sent is untrusted: it only ever appears as a JSON string value, never as part of the line itself,
so a device name with a newline cannot forge a second log line.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

MAX_TEXT = 200  # a hostile sender must not fill the log with one field


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    clean = {k: (v[:MAX_TEXT] if isinstance(v, str) else v) for k, v in fields.items()}
    logger.log(level, event, extra={"fields": clean})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str, ensure_ascii=True)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("pulseguard")
    root.handlers[:] = [handler]
    root.setLevel(level)
