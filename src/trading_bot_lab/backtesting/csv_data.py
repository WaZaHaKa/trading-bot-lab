"""Validated CSV market-data boundary."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path

from trading_bot_lab.domain import (
    DataValidationError,
    DataWarning,
    MarketBar,
    MarketDataMetadata,
    MarketDataSet,
    PriceBar,
    WarningCode,
)


class MissingVolumePolicy(StrEnum):
    """How the CSV boundary handles absent volume."""

    ALLOW = "allow"
    WARN = "warn"
    REJECT = "reject"


class GapPolicy(StrEnum):
    """How the CSV boundary handles unexpectedly large timestamp gaps."""

    WARN = "warn"
    REJECT = "reject"


@dataclass(frozen=True)
class CsvDataConfig:
    """Typed CSV validation policy."""

    expected_symbol: str | None = None
    timeframe_seconds: int = 86_400
    missing_volume_policy: MissingVolumePolicy = MissingVolumePolicy.WARN
    max_gap_seconds: int | None = 7 * 86_400
    gap_policy: GapPolicy = GapPolicy.WARN

    def __post_init__(self) -> None:
        if self.timeframe_seconds <= 0:
            raise ValueError("timeframe_seconds must be positive")
        if self.max_gap_seconds is not None and self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive when set")
        if self.expected_symbol is not None and not self.expected_symbol.strip():
            raise ValueError("expected_symbol must be non-empty when set")


def load_market_data_csv(
    path: str | Path,
    *,
    config: CsvDataConfig | None = None,
) -> MarketDataSet:
    """Load one symbol of UTC-normalized OHLCV data from a local CSV.

    Required columns are `symbol`, `close`, and exactly one of `timestamp` or
    `date`.  A calendar `date` is normalized to midnight UTC.  A `timestamp`
    must be ISO-8601 and include a timezone.  Rows must already be strictly
    ascending; the loader never sorts input because sorting can hide upstream
    data defects.
    """

    validation = config or CsvDataConfig()
    csv_path = Path(path)
    try:
        handle = csv_path.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise DataValidationError(f"unable to open market-data CSV {csv_path}: {exc}") from exc

    warnings: list[DataWarning] = []
    with handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        missing = {"symbol", "close"} - fieldnames
        if missing:
            joined = ", ".join(sorted(missing))
            raise DataValidationError(f"{csv_path} is missing required columns: {joined}")
        timestamp_columns = {"timestamp", "date"} & fieldnames
        if len(timestamp_columns) != 1:
            raise DataValidationError(
                f"{csv_path} must contain exactly one timestamp column: timestamp or date"
            )

        bars: list[MarketBar] = []
        missing_volume_seen = False
        for row_number, row in enumerate(reader, start=2):
            bar, missing_volume = _parse_row(
                csv_path,
                row_number,
                row,
                timestamp_column=next(iter(timestamp_columns)),
                timeframe_seconds=validation.timeframe_seconds,
            )
            if validation.expected_symbol is not None:
                expected = validation.expected_symbol.strip().upper()
                if bar.symbol != expected:
                    raise DataValidationError(
                        f"{csv_path}:{row_number} symbol {bar.symbol!r} does not match "
                        f"expected symbol {expected!r}"
                    )
            if missing_volume:
                missing_volume_seen = True
                if validation.missing_volume_policy is MissingVolumePolicy.REJECT:
                    raise DataValidationError(f"{csv_path}:{row_number} is missing volume")
            bars.append(bar)

    if not bars:
        raise DataValidationError(f"{csv_path} did not contain any market-data rows")

    _validate_single_symbol_timeline(csv_path, bars, validation, warnings)
    if missing_volume_seen and validation.missing_volume_policy is MissingVolumePolicy.WARN:
        warnings.append(
            DataWarning(
                code=WarningCode.MISSING_VOLUME,
                message="one or more bars have missing volume; price-only research may continue",
            )
        )

    metadata = MarketDataMetadata(
        source=csv_path.as_posix(),
        symbol=bars[0].symbol,
        row_count=len(bars),
        start_timestamp=bars[0].timestamp,
        end_timestamp=bars[-1].timestamp,
        timeframe_seconds=validation.timeframe_seconds,
    )
    return MarketDataSet(tuple(bars), metadata, tuple(warnings))


def load_price_bars_csv(
    path: str | Path,
    *,
    expected_symbol: str | None = None,
    max_calendar_gap_days: int = 7,
) -> tuple[PriceBar, ...]:
    """Compatibility loader returning only bars.

    The original API treated large gaps as fatal.  That behavior remains here;
    new callers should use :func:`load_market_data_csv` to receive typed gap
    warnings instead.
    """

    if max_calendar_gap_days <= 0:
        raise ValueError("max_calendar_gap_days must be positive")
    dataset = load_market_data_csv(
        path,
        config=CsvDataConfig(
            expected_symbol=expected_symbol,
            missing_volume_policy=MissingVolumePolicy.ALLOW,
            max_gap_seconds=max_calendar_gap_days * 86_400,
            gap_policy=GapPolicy.REJECT,
        ),
    )
    return dataset.bars


def _parse_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
    *,
    timestamp_column: str,
    timeframe_seconds: int,
) -> tuple[MarketBar, bool]:
    symbol = (row.get("symbol") or "").strip().upper()
    if not symbol:
        raise DataValidationError(f"{path}:{row_number} has an empty symbol")

    bar_timestamp = _parse_timestamp(path, row_number, row.get(timestamp_column), timestamp_column)
    close = _parse_required_positive_float(path, row_number, row.get("close"), "close")
    open_price = _parse_optional_positive_float(path, row_number, row.get("open"), "open")
    high = _parse_optional_positive_float(path, row_number, row.get("high"), "high")
    low = _parse_optional_positive_float(path, row_number, row.get("low"), "low")
    volume, missing_volume = _parse_optional_volume(path, row_number, row.get("volume"))

    try:
        bar = MarketBar(
            timestamp=bar_timestamp,
            symbol=symbol,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            timeframe_seconds=timeframe_seconds,
        )
    except ValueError as exc:
        raise DataValidationError(f"{path}:{row_number} has inconsistent OHLCV: {exc}") from exc
    return bar, missing_volume


def _parse_timestamp(path: Path, row_number: int, value: str | None, column: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise DataValidationError(f"{path}:{row_number} has an empty {column}")
    try:
        if column == "date":
            parsed_date = date.fromisoformat(raw)
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DataValidationError(f"{path}:{row_number} has an invalid ISO-8601 {column}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataValidationError(f"{path}:{row_number} timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _parse_required_positive_float(
    path: Path,
    row_number: int,
    value: str | None,
    column: str,
) -> float:
    raw = (value or "").strip()
    if not raw:
        raise DataValidationError(f"{path}:{row_number} has an empty {column}")
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise DataValidationError(f"{path}:{row_number} has a non-numeric {column}") from exc
    if not isfinite(parsed) or parsed <= 0:
        raise DataValidationError(f"{path}:{row_number} has a non-positive or non-finite {column}")
    return parsed


def _parse_optional_positive_float(
    path: Path,
    row_number: int,
    value: str | None,
    column: str,
) -> float | None:
    if value is None or not value.strip():
        return None
    return _parse_required_positive_float(path, row_number, value, column)


def _parse_optional_volume(
    path: Path,
    row_number: int,
    value: str | None,
) -> tuple[float | None, bool]:
    if value is None or not value.strip():
        return None, True
    try:
        parsed = float(value)
    except ValueError as exc:
        raise DataValidationError(f"{path}:{row_number} has non-numeric volume") from exc
    if not isfinite(parsed) or parsed < 0:
        raise DataValidationError(f"{path}:{row_number} has negative or non-finite volume")
    return parsed, False


def _validate_single_symbol_timeline(
    path: Path,
    bars: list[MarketBar],
    config: CsvDataConfig,
    warnings: list[DataWarning],
) -> None:
    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        joined = ", ".join(sorted(symbols))
        raise DataValidationError(f"{path} must contain exactly one symbol; found {joined}")

    previous: datetime | None = None
    for bar in bars:
        if previous is not None:
            if bar.timestamp == previous:
                raise DataValidationError(
                    f"{path} has a duplicated timestamp: {bar.timestamp.isoformat()}"
                )
            if bar.timestamp < previous:
                raise DataValidationError(f"{path} must be sorted ascending by timestamp")
            gap_seconds = int((bar.timestamp - previous).total_seconds())
            if config.max_gap_seconds is not None and gap_seconds > config.max_gap_seconds:
                message = (
                    f"{path} has a large time gap of {gap_seconds} seconds ending "
                    f"{bar.timestamp.isoformat()}"
                )
                if config.gap_policy is GapPolicy.REJECT:
                    gap_days = gap_seconds // 86_400
                    raise DataValidationError(
                        f"{path} has a large date gap of {gap_days} days ending "
                        f"{bar.timestamp.date().isoformat()}"
                    )
                warnings.append(
                    DataWarning(
                        code=WarningCode.LARGE_TIME_GAP,
                        message=message,
                        timestamp=bar.timestamp,
                    )
                )
        previous = bar.timestamp


__all__ = [
    "CsvDataConfig",
    "GapPolicy",
    "MissingVolumePolicy",
    "PriceBar",
    "load_market_data_csv",
    "load_price_bars_csv",
]
