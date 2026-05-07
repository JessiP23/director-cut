"""Logger configuration — prefers structlog; falls back if venv is incomplete."""

from __future__ import annotations

import logging
from typing import Any

try:
    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    _STRUCTLOG = True
except ImportError:
    structlog = None  # type: ignore[assignment,misc]
    _STRUCTLOG = False


class _FallbackLogger:
    """Enough API for `log.info("event", key=val)` used across the backend."""

    __slots__ = ("_log",)

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    @staticmethod
    def _line(event: str, kw: dict[str, Any]) -> str:
        if not kw:
            return event
        bits = " ".join(f"{k}={v!r}" for k, v in kw.items())
        return f"{event} | {bits}"

    def info(self, event: str, **kw: Any) -> None:
        self._log.info(self._line(event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self._log.warning(self._line(event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self._log.error(self._line(event, kw))


def get_logger(name: str):
    if _STRUCTLOG:
        return structlog.get_logger(name)
    return _FallbackLogger(name)
