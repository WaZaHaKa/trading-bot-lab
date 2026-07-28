from __future__ import annotations

from datetime import date
from math import isfinite

from AlgorithmImports import (
    AccountType,
    BrokerageName,
    DataNormalizationMode,
    QCAlgorithm,
    Resolution,
    Slice,
    TimeZones,
)


def parse_iso_date(raw: str, name: str) -> date:
    """Parse a strict ISO date without guessing locale or time zone."""

    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def parse_positive_float(raw: str, name: str) -> float:
    """Parse a finite value that must be greater than zero."""

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


class SkeletonBacktest(QCAlgorithm):
    """Deterministic no-order subscription smoke test."""

    maximum_allowed_allocation = 0.05

    def initialize(self) -> None:
        if self.live_mode:
            raise RuntimeError("SkeletonBacktest is cloud-backtest-only; live mode is forbidden")

        start = parse_iso_date(self.get_parameter("start-date", "2023-01-01"), "start-date")
        end = parse_iso_date(self.get_parameter("end-date", "2023-03-31"), "end-date")
        if end <= start:
            raise ValueError("end-date must be later than start-date")

        initial_cash = parse_positive_float(
            self.get_parameter("initial-cash", "100000"),
            "initial-cash",
        )
        allocation_cap = parse_positive_float(
            self.get_parameter("maximum-allocation", "0.05"),
            "maximum-allocation",
        )
        if allocation_cap > self.maximum_allowed_allocation:
            raise ValueError("maximum-allocation cannot exceed 0.05")

        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_time_zone(TimeZones.UTC)
        self.set_cash(initial_cash)
        self.set_brokerage_model(
            BrokerageName.QUANT_CONNECT_BROKERAGE,
            AccountType.CASH,
        )

        security = self.add_equity(
            "SPY",
            Resolution.DAILY,
            fill_forward=False,
            data_normalization_mode=DataNormalizationMode.ADJUSTED,
        )
        security.set_leverage(1.0)
        self._symbol = security.symbol
        self._allocation_cap = allocation_cap
        self._completed_bars = 0
        self.set_benchmark(self._symbol)

    def on_data(self, data: Slice) -> None:
        """Observe completed daily bars and intentionally submit no orders."""

        if data.contains_key(self._symbol) and data[self._symbol] is not None:
            self._completed_bars += 1

    def on_end_of_algorithm(self) -> None:
        if self.transactions.get_open_orders():
            raise RuntimeError("no-order skeleton unexpectedly has an open order")
        self.debug(
            f"SkeletonBacktest completed {self._completed_bars} SPY bars; "
            f"orders=0; allocation_cap={self._allocation_cap:.2%}"
        )
