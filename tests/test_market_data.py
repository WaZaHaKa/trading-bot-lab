from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_bot_lab.backtesting import (
    CsvDataConfig,
    GapPolicy,
    MissingVolumePolicy,
    load_market_data_csv,
    load_price_bars_csv,
)
from trading_bot_lab.domain import DataValidationError, MarketBar, WarningCode

SAMPLE_DATA = Path("data/sample/synthetic_spy_daily.csv")


def write_csv(tmp_path: Path, text: str, name: str = "bars.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_sample_csv_loads_as_utc_ohlcv() -> None:
    dataset = load_market_data_csv(
        SAMPLE_DATA,
        config=CsvDataConfig(expected_symbol="SPY"),
    )

    assert len(dataset.bars) == 15
    assert dataset.metadata.timezone == "UTC"
    assert dataset.metadata.row_count == 15
    assert dataset.bars[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert dataset.bars[0].open == 99.5
    assert dataset.bars[0].volume == 1000
    assert dataset.warnings == ()


def test_offset_timestamp_is_normalized_to_utc(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "timestamp,symbol,open,high,low,close,volume\n"
        "2024-01-01T02:00:00+02:00,SPY,99,101,98,100,10\n",
    )

    dataset = load_market_data_csv(path)

    assert dataset.bars[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)


def test_market_bar_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        MarketBar(timestamp=datetime(2024, 1, 1), symbol="SPY", close=100)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("date,symbol\n2024-01-01,SPY\n", "missing required columns: close"),
        ("symbol,close\nSPY,100\n", "exactly one timestamp column"),
        (
            "timestamp,date,symbol,close\n2024-01-01T00:00:00Z,2024-01-01,SPY,100\n",
            "exactly one timestamp column",
        ),
    ],
)
def test_required_columns_fail_closed(tmp_path: Path, header: str, message: str) -> None:
    with pytest.raises(DataValidationError, match=message):
        load_market_data_csv(write_csv(tmp_path, header))


def test_duplicate_timestamp_is_rejected(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "date,symbol,close\n2024-01-01,SPY,100\n2024-01-01,SPY,101\n",
    )

    with pytest.raises(DataValidationError, match="duplicated timestamp"):
        load_market_data_csv(path)


def test_unsorted_timestamp_is_rejected_without_sorting(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "date,symbol,close\n2024-01-02,SPY,101\n2024-01-01,SPY,100\n",
    )

    with pytest.raises(DataValidationError, match="sorted ascending"):
        load_market_data_csv(path)


@pytest.mark.parametrize("bad_price", ["0", "-1", "nan", "inf"])
def test_non_positive_and_non_finite_prices_are_rejected(
    tmp_path: Path,
    bad_price: str,
) -> None:
    path = write_csv(
        tmp_path,
        f"date,symbol,close\n2024-01-01,SPY,{bad_price}\n",
    )

    with pytest.raises(DataValidationError, match="non-positive or non-finite close"):
        load_market_data_csv(path)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("100,99,98,99", "high must be at least"),
        ("100,101,100,99", "low must be at most"),
        ("100,98,99,100", "high must be greater"),
    ],
)
def test_ohlc_consistency_is_rejected(tmp_path: Path, row: str, message: str) -> None:
    path = write_csv(
        tmp_path,
        f"date,symbol,open,high,low,close\n2024-01-01,SPY,{row}\n",
    )

    with pytest.raises(DataValidationError, match=message):
        load_market_data_csv(path)


@pytest.mark.parametrize("bad_volume", ["-1", "nan", "inf", "abc"])
def test_invalid_volume_is_rejected(tmp_path: Path, bad_volume: str) -> None:
    path = write_csv(
        tmp_path,
        f"date,symbol,close,volume\n2024-01-01,SPY,100,{bad_volume}\n",
    )

    with pytest.raises(DataValidationError, match="volume"):
        load_market_data_csv(path)


def test_missing_volume_policy_can_warn_allow_or_reject(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "date,symbol,close,volume\n2024-01-01,SPY,100,\n")

    warned = load_market_data_csv(
        path,
        config=CsvDataConfig(missing_volume_policy=MissingVolumePolicy.WARN),
    )
    allowed = load_market_data_csv(
        path,
        config=CsvDataConfig(missing_volume_policy=MissingVolumePolicy.ALLOW),
    )

    assert warned.warnings[0].code is WarningCode.MISSING_VOLUME
    assert allowed.warnings == ()
    with pytest.raises(DataValidationError, match="missing volume"):
        load_market_data_csv(
            path,
            config=CsvDataConfig(missing_volume_policy=MissingVolumePolicy.REJECT),
        )


def test_large_gap_can_warn_or_reject(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "date,symbol,close\n2024-01-01,SPY,100\n2024-01-20,SPY,101\n",
    )
    warned = load_market_data_csv(
        path,
        config=CsvDataConfig(
            missing_volume_policy=MissingVolumePolicy.ALLOW,
            max_gap_seconds=7 * 86_400,
            gap_policy=GapPolicy.WARN,
        ),
    )

    assert warned.warnings[0].code is WarningCode.LARGE_TIME_GAP
    with pytest.raises(DataValidationError, match="large date gap of 19 days"):
        load_price_bars_csv(path)


def test_expected_symbol_mismatch_is_not_silently_filtered(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "date,symbol,close\n2024-01-01,QQQ,100\n")

    with pytest.raises(DataValidationError, match="does not match expected symbol"):
        load_market_data_csv(path, config=CsvDataConfig(expected_symbol="SPY"))


def test_multiple_symbols_are_rejected(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "date,symbol,close\n2024-01-01,SPY,100\n2024-01-02,QQQ,101\n",
    )

    with pytest.raises(DataValidationError, match="exactly one symbol"):
        load_market_data_csv(path)


def test_timeframe_is_explicit_in_bars_and_metadata(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "timestamp,symbol,close\n2024-01-01T00:01:00Z,BTCUSD,100\n",
    )
    dataset = load_market_data_csv(
        path,
        config=CsvDataConfig(
            timeframe_seconds=60,
            missing_volume_policy=MissingVolumePolicy.ALLOW,
        ),
    )

    assert dataset.metadata.timeframe_seconds == 60
    assert dataset.bars[0].timeframe_seconds == 60
