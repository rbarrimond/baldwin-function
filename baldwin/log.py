"""Structured JSON logging helpers for the Baldwin package."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object on stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_root() -> None:
    """Configure the ``baldwin`` package logger once per process.

    Attaches a JSON stdout handler to the ``baldwin`` root logger and disables
    propagation so records are not forwarded to a second handler registered by
    the host runtime (Azure Functions worker, pytest, etc.).
    """
    root = logging.getLogger("baldwin")
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a named logger that emits JSON to stdout.

    Always pass ``__name__`` as *name* so log records carry the originating
    module path.  The ``baldwin`` root logger is configured on the first call.
    """
    _configure_root()
    return logging.getLogger(name)
