"""Local event log.

Qt-free by design: the UI subscribes with a plain callable, which keeps the
core importable in headless tests and makes the log panel a pure consumer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..domain import EventLevel, utc_now
from ..persistence import Database

__all__ = ["EventLog", "LogRecord", "NullEventLog"]

logger = logging.getLogger(__name__)

#: Subscriber signature: receives each :class:`LogRecord` as it is emitted.
Listener = Callable[["LogRecord"], None]


def _retention_bounds() -> tuple[int, int]:
    """(max_events, prune_interval) from the config module."""
    from .config import EVENT_PRUNE_INTERVAL, MAX_APP_EVENTS

    return MAX_APP_EVENTS, EVENT_PRUNE_INTERVAL


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One timestamped application event."""

    ts: str
    level: EventLevel
    source: str
    message: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def format_line(self) -> str:
        """Single-line rendering used by the log panel and file logs."""
        origin = f"[{self.source}] " if self.source else ""
        return f"{self.ts}  {self.level.value:<7} {origin}{self.message}"


class EventLog:
    """Append-only event log with optional SQLite persistence and listeners."""

    def __init__(
        self,
        database: Database | None = None,
        echo: bool = False,
        *,
        retention_enabled: bool = True,
    ) -> None:
        self._database = database
        self._listeners: list[Listener] = []
        self._echo = echo
        self._history: list[LogRecord] = []
        self._retention_enabled = retention_enabled
        self._appends_since_prune = 0

    # -- subscription ----------------------------------------------------
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register ``listener``; returns a function that unsubscribes it."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # -- emission ---------------------------------------------------------
    def emit(
        self,
        message: str,
        *,
        level: EventLevel | str = EventLevel.INFO,
        source: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> LogRecord:
        """Record an event, persist it and notify listeners."""
        if not isinstance(level, EventLevel):
            level = EventLevel(str(level))
        record = LogRecord(
            ts=utc_now(),
            level=level,
            source=source,
            message=message,
            payload=dict(payload or {}),
        )
        self._history.append(record)

        if self._database is not None:
            try:
                self._database.log_event(
                    record.message,
                    level=record.level,
                    source=record.source,
                    payload=record.payload or None,
                )
            except Exception:  # pragma: no cover - logging must never crash the UI
                logger.exception("Failed to persist event: %s", record.message)
            self._maybe_prune_database()

        if self._echo:
            logger.log(
                {
                    EventLevel.DEBUG: logging.DEBUG,
                    EventLevel.INFO: logging.INFO,
                    EventLevel.WARNING: logging.WARNING,
                    EventLevel.ERROR: logging.ERROR,
                }[record.level],
                "%s%s",
                record.format_line(),
                "",
            )

        for listener in tuple(self._listeners):
            try:
                listener(record)
            except Exception:  # pragma: no cover - a bad listener must not break logging
                logger.exception("Event listener failed")

        return record

    # -- convenience ------------------------------------------------------
    def _maybe_prune_database(self) -> None:
        """Opportunistic bounded retention (Session 007).

        Cheap counting on every append; the actual DELETE runs at most once
        per ``EVENT_PRUNE_INTERVAL`` appends AND only when the table is over
        ``MAX_APP_EVENTS``.  Retention never runs inside the append
        transaction and never touches anything but ``app_events``.  Failures
        are logged, never raised — pruning must not break logging.
        """
        if not self._retention_enabled or self._database is None:
            return
        self._appends_since_prune += 1
        if self._appends_since_prune < _retention_bounds()[1]:
            return
        self._appends_since_prune = 0
        try:
            max_events, _ = _retention_bounds()
            if self._database.event_count() > max_events:
                deleted = self._database.prune_app_events(max_events)
                if deleted:
                    logger.info(
                        "Event retention: pruned %d old app_events rows "
                        "(bound: %d).",
                        deleted,
                        max_events,
                    )
        except Exception:  # pragma: no cover - retention must never break logging
            logger.exception("app_events retention failed")

    def debug(self, message: str, **kwargs: Any) -> LogRecord:
        return self.emit(message, level=EventLevel.DEBUG, **kwargs)

    def info(self, message: str, **kwargs: Any) -> LogRecord:
        return self.emit(message, level=EventLevel.INFO, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> LogRecord:
        return self.emit(message, level=EventLevel.WARNING, **kwargs)

    def error(self, message: str, **kwargs: Any) -> LogRecord:
        return self.emit(message, level=EventLevel.ERROR, **kwargs)

    # -- introspection -----------------------------------------------------
    def history(self) -> tuple[LogRecord, ...]:
        return tuple(self._history)

    def __len__(self) -> int:
        return len(self._history)


class NullEventLog(EventLog):
    """Event log that keeps nothing — useful as a default dependency."""

    def __init__(self) -> None:
        super().__init__(database=None, echo=False)
