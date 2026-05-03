"""Structured JSON logging helpers for the Baldwin package."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """Bind a trace ID to the current context.

    The value propagates automatically to threads submitted via
    ``concurrent.futures.ThreadPoolExecutor`` because Python copies the
    active context on each ``submit`` call.
    """
    _trace_id_var.set(trace_id)


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = _trace_id_var.get()
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


class _LevelRoutingHandler(logging.Handler):
    """Route records to stderr (WARNING+) or stdout (DEBUG/INFO).

    The Azure Functions local host re-emits every stdout line with INFO-level
    ANSI colour because it cannot inspect raw text for the actual severity.
    Sending WARNING and above to stderr lets the terminal apply the correct
    colour without altering the JSON payload.
    """

    _formatter = _JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self._formatter.format(record)
            stream = sys.stderr if record.levelno >= logging.WARNING else sys.stdout
            stream.write(line + "\n")
            stream.flush()
        except OSError:
            self.handleError(record)


def _configure_root() -> None:
    """Configure the ``baldwin`` package logger once per process.

    Attaches a level-routing JSON handler to the ``baldwin`` root logger and
    disables propagation so records are not forwarded to a second handler
    registered by the host runtime (Azure Functions worker, pytest, etc.).

    The effective log level is read from the ``BALDWIN_LOG_LEVEL`` environment
    variable (e.g. ``DEBUG``, ``INFO``, ``WARNING``).  Defaults to ``WARNING``
    when the variable is absent or invalid so that DEBUG/INFO records are not
    emitted in production unless explicitly opted in.
    """
    root = logging.getLogger("baldwin")
    if root.handlers:
        return
    level_name = os.environ.get("BALDWIN_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    root.setLevel(level)
    root.addHandler(_LevelRoutingHandler())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a named logger that emits JSON to the appropriate stream.

    Always pass ``__name__`` as *name* so log records carry the originating
    module path.  The ``baldwin`` root logger is configured on the first call.
    """
    _configure_root()
    return logging.getLogger(name)
