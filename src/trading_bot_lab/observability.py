"""Lightweight structured local logging with bounded file rotation."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path

from trading_bot_lab.domain import DataWarning, WarningCode

DEFAULT_LOG_MAX_BYTES = 2_000_000
DEFAULT_LOG_BACKUP_COUNT = 2

_ALLOWED_EVENT_FIELDS = frozenset(
    {
        "event_schema_version",
        "event",
        "session_id",
        "strategy_name",
        "symbol",
        "event_timestamp",
        "halt_state",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "signal_timestamp",
        "target_weight",
        "intent_id",
        "execution_timestamp",
        "side",
        "quantity",
        "reference_price",
        "estimated_execution_price",
        "estimated_fee",
        "risk_reasons",
        "risk_metrics",
        "fill_id",
        "execution_price",
        "fee",
        "slippage_cost",
        "resulting_cash",
        "resulting_quantity",
        "cash",
        "average_cost",
        "equity",
        "exposure",
        "drawdown",
        "reason",
        "from_status",
        "to_status",
    }
)
_ALLOWED_EVENTS = frozenset(
    {
        "session_created",
        "data_validated",
        "session_started",
        "bar_received",
        "signal_generated",
        "intent_created",
        "risk_accepted",
        "risk_rejected",
        "fill_created",
        "portfolio_updated",
        "session_paused",
        "session_resumed",
        "kill_switch_activated",
        "session_stopped",
        "session_completed",
        "session_failed",
        "session_halted",
        "pending_signal_expired",
    }
)
_REQUIRED_EVENT_FIELDS = frozenset(
    {
        "event_schema_version",
        "event",
        "session_id",
        "strategy_name",
        "symbol",
        "event_timestamp",
    }
)


class _PropagatingRotatingFileHandler(RotatingFileHandler):
    """Rotate local logs while allowing the simulation to disclose I/O failure."""

    def handleError(self, record: logging.LogRecord) -> None:
        del record
        error = sys.exception()
        if error is None:
            raise RuntimeError("structured event delivery failed without an active exception")
        raise error


class StructuredEventSink:
    """Callable JSON-lines event sink used by simulation engines."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_LOG_MAX_BYTES,
        backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    ) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if type(backup_count) is not int or backup_count <= 0:
            raise ValueError("backup_count must be a positive integer")
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = log_path
        self.artifact_paths = structured_log_artifact_paths(
            log_path,
            backup_count=backup_count,
        )
        self._logger = logging.getLogger(f"trading_bot_lab.session.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = _PropagatingRotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.handlers.clear()
        self._logger.addHandler(handler)
        self.close_warning: DataWarning | None = None
        self._closed = False

    def __call__(self, event: dict[str, object]) -> None:
        """Serialize one allowlisted engine event without secret lookup."""

        if self._closed:
            raise RuntimeError("structured event sink is closed")
        missing = _REQUIRED_EVENT_FIELDS - set(event)
        if missing:
            joined = ", ".join(sorted(missing))
            raise ValueError(f"structured event is missing required fields: {joined}")
        unexpected = set(event) - _ALLOWED_EVENT_FIELDS
        if unexpected:
            joined = ", ".join(sorted(unexpected))
            raise ValueError(f"structured event contains non-allowlisted fields: {joined}")
        if event["event_schema_version"] != "1.0.0":
            raise ValueError("structured event has an unsupported schema version")
        if event["event"] not in _ALLOWED_EVENTS:
            raise ValueError("structured event has a non-allowlisted event name")
        self._logger.info(json.dumps(event, sort_keys=True, default=_json_default, allow_nan=False))

    def close(self) -> DataWarning | None:
        """Best-effort flush/close, returning a disclosure warning on failure."""

        if self._closed:
            return self.close_warning
        error_types: set[str] = set()
        for handler in tuple(self._logger.handlers):
            for operation in (handler.flush, handler.close):
                try:
                    operation()
                except Exception as error:
                    error_types.add(type(error).__name__)
            self._logger.removeHandler(handler)
        self._closed = True
        if error_types:
            joined_types = ", ".join(sorted(error_types))
            self.close_warning = DataWarning(
                code=WarningCode.EVENT_SINK_FAILURE,
                message=(
                    "local structured event sink close failed with "
                    f"{joined_types}; events may be missing"
                ),
            )
        return self.close_warning

    def __enter__(self) -> StructuredEventSink:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def structured_log_artifact_paths(
    path: str | Path,
    *,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> tuple[Path, ...]:
    """Return the primary log and every filename reserved for rotation."""

    if type(backup_count) is not int or backup_count <= 0:
        raise ValueError("backup_count must be a positive integer")
    primary = Path(path)
    backups = tuple(Path(f"{primary}.{index}") for index in range(1, backup_count + 1))
    return (primary, *backups)


__all__ = [
    "DEFAULT_LOG_BACKUP_COUNT",
    "DEFAULT_LOG_MAX_BYTES",
    "StructuredEventSink",
    "structured_log_artifact_paths",
]
