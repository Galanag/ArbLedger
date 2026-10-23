"""Structured JSON logging and lightweight correlation context support."""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("arbledger_correlation_id", default="-")


@contextmanager
def correlation_scope(correlation_id: str) -> Iterator[None]:
    """Set a correlation identifier for log records in the current async context."""
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Format log records as one JSON object per line for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": _correlation_id.get(),
        }
        for key in ("worker_id", "venue", "opportunity_id", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    """Install a single structured stderr handler on the root logger."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(level)
