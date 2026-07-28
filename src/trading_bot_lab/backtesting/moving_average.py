"""Deterministic moving-average baseline strategy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from trading_bot_lab.domain import MarketBar, Signal


@dataclass(frozen=True)
class MovingAverageStrategy:
    """Close-only moving-average target generator.

    A target returned for day N is intended for execution on day N+1 by the
    harness, which avoids same-bar lookahead in the MVP.
    """

    fast_window: int = 3
    slow_window: int = 5
    target_weight: float = 0.10

    @property
    def name(self) -> str:
        """Return the stable report/log identifier."""

        return "moving_average"

    @property
    def configuration(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        return (
            ("fast_window", self.fast_window),
            ("slow_window", self.slow_window),
            ("target_weight", self.target_weight),
        )

    def __post_init__(self) -> None:
        if type(self.fast_window) is not int or self.fast_window <= 0:
            raise ValueError("fast_window must be a positive integer")
        if type(self.slow_window) is not int or self.slow_window <= 0:
            raise ValueError("slow_window must be a positive integer")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        if (
            isinstance(self.target_weight, bool)
            or not isinstance(self.target_weight, (int, float))
            or not isfinite(self.target_weight)
            or self.target_weight < 0
            or self.target_weight > 1
        ):
            raise ValueError("target_weight must be finite and between 0 and 1")

    def target_for_closes(self, closes: Sequence[float]) -> float:
        """Return the desired long-only portfolio weight from historical closes."""

        if len(closes) < self.slow_window:
            return 0.0

        fast_average = sum(closes[-self.fast_window :]) / self.fast_window
        slow_average = sum(closes[-self.slow_window :]) / self.slow_window
        if fast_average > slow_average:
            return self.target_weight
        return 0.0

    def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
        """Generate a target from the supplied historical prefix only."""

        if not history:
            raise ValueError("history must not be empty")
        latest = history[-1]
        return Signal(
            timestamp=latest.timestamp,
            symbol=latest.symbol,
            target_weight=self.target_for_closes([bar.close for bar in history]),
            strategy_name=self.name,
        )


@dataclass(frozen=True)
class NoTradeStrategy:
    """Control strategy that always requests a flat portfolio."""

    @property
    def name(self) -> str:
        return "no_trade"

    @property
    def configuration(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        return ()

    def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
        if not history:
            raise ValueError("history must not be empty")
        latest = history[-1]
        return Signal(
            timestamp=latest.timestamp,
            symbol=latest.symbol,
            target_weight=0.0,
            strategy_name=self.name,
        )
