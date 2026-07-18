from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from trading_bot_lab.domain import MarketBar, Signal


def make_bars(
    closes: Sequence[float],
    *,
    opens: Sequence[float] | None = None,
    symbol: str = "SPY",
) -> tuple[MarketBar, ...]:
    selected_opens = list(opens) if opens is not None else list(closes)
    if len(selected_opens) != len(closes):
        raise ValueError("opens and closes must have the same length")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketBar(
            timestamp=start + timedelta(days=index),
            symbol=symbol,
            open=open_price,
            high=max(open_price, close) + 1,
            low=min(open_price, close) - 1,
            close=close,
            volume=1_000 + index,
        )
        for index, (open_price, close) in enumerate(zip(selected_opens, closes, strict=True))
    )


@dataclass
class TargetSequenceStrategy:
    targets: tuple[float, ...]
    history_lengths: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "target_sequence"

    def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
        self.history_lengths.append(len(history))
        index = min(len(history) - 1, len(self.targets) - 1)
        latest = history[-1]
        return Signal(
            timestamp=latest.timestamp,
            symbol=latest.symbol,
            target_weight=self.targets[index],
            strategy_name=self.name,
        )
