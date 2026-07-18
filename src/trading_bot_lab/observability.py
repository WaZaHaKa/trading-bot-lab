"""Lightweight structured local logging with bounded file rotation."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path


class StructuredEventSink:
    """Callable JSON-lines event sink used by simulation engines."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 2_000_000,
        backup_count: int = 2,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if backup_count < 0:
            raise ValueError("backup_count must be non-negative")
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = log_path
        self._logger = logging.getLogger(f"trading_bot_lab.session.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.handlers.clear()
        self._logger.addHandler(handler)

    def __call__(self, event: dict[str, object]) -> None:
        """Serialize one allowlisted engine event without secret lookup."""

        self._logger.info(json.dumps(event, sort_keys=True, default=_json_default))

    def close(self) -> None:
        """Flush and close local file handlers."""

        for handler in tuple(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)

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


__all__ = ["StructuredEventSink"]
