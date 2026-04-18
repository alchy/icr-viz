"""Central logging configuration for piano_web (and piano_core via propagation).

Keep logging verbose by default — the project's debugging strategy relies on
post-mortem log analysis, especially once operators and consensus flows come
online in i2+.

Invocation:
    - FastAPI app startup calls `configure_logging()` once.
    - Tests call it too if they need log capture (pytest caplog fixture works
      either way — this just ensures levels are set).

Environment variables:
    ICR_VIZ_LOG_LEVEL   override root level. Default: INFO.
                        Accepts: DEBUG, INFO, WARNING, ERROR.
    ICR_VIZ_LOG_JSON    set to "1"/"true" for JSON-structured output.
                        Default: human-readable text (dev-friendly).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class _TextFormatter(logging.Formatter):
    """Human-readable format with structured `extra` fields appended."""

    default_fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.default_fmt, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _collect_extras(record)
        if extras:
            suffix = " ".join(f"{k}={_repr_short(v)}" for k, v in extras.items())
            return f"{base}  [{suffix}]"
        return base


class _JsonFormatter(logging.Formatter):
    """JSON-structured formatter — one line per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%03d"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        payload.update(_collect_extras(record))
        return json.dumps(payload, ensure_ascii=False, default=str)


_STANDARD_RECORD_FIELDS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


def _collect_extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        k: v for k, v in record.__dict__.items()
        if k not in _STANDARD_RECORD_FIELDS and not k.startswith("_")
    }


def _repr_short(v: Any, *, max_len: int = 80) -> str:
    s = str(v)
    if len(s) > max_len:
        s = s[: max_len - 1] + "..."
    return s


_configured = False


def configure_logging(*, force: bool = False) -> None:
    """Set up root logger. Idempotent — subsequent calls are no-ops unless `force=True`."""
    global _configured
    if _configured and not force:
        return

    level_name = os.environ.get("ICR_VIZ_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    use_json = os.environ.get("ICR_VIZ_LOG_JSON", "").lower() in ("1", "true", "yes")
    formatter: logging.Formatter = _JsonFormatter() if use_json else _TextFormatter()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers (relevant on force=True) so we don't double-log.
    root.handlers = [handler]

    # Quiet a few chatty third-party loggers (tune as ecosystem grows).
    for noisy in ("asyncio", "aiosqlite", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).debug(
        "logging configured", extra={"level": level_name, "json": use_json}
    )
