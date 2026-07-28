"""Deterministic, path-safe provenance helpers for local market data."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path, PureWindowsPath

from trading_bot_lab.domain import MarketBar


def content_sha256(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of the exact supplied bytes."""

    return sha256(data).hexdigest()


def bars_content_sha256(bars: tuple[MarketBar, ...]) -> str:
    """Hash a canonical representation of validated in-memory bars."""

    payload = [
        {
            "timestamp": bar.timestamp.isoformat(),
            "symbol": bar.symbol,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "timeframe_seconds": bar.timeframe_seconds,
        }
        for bar in bars
    ]
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return content_sha256(encoded)


def safe_source_filename(value: str | Path) -> str:
    """Strip directory components without depending on the host path flavor."""

    selected = PureWindowsPath(str(value).replace("/", "\\")).name.strip()
    if selected in {"", ".", ".."}:
        return "in_memory"
    return selected


__all__ = ["bars_content_sha256", "content_sha256", "safe_source_filename"]
