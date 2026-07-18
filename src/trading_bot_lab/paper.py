"""Entirely local historical replay for simulated paper-trading research."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from trading_bot_lab.backtesting.engine import (
    ENGINE_VERSION,
    BacktestConfig,
    EventSink,
    SimulationEngine,
)
from trading_bot_lab.domain import (
    DataWarning,
    MarketBar,
    MarketDataMetadata,
    PaperSessionStatus,
    PaperSessionSummary,
    SessionTransition,
    Strategy,
)
from trading_bot_lab.risk import RiskPolicy


@dataclass(frozen=True)
class PaperReplayConfig:
    """Deterministic replay scheduling configuration."""

    replay_speed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.replay_speed_seconds <= 60:
            raise ValueError("replay_speed_seconds must be between 0 and 60")


class HistoricalReplaySession:
    """Pauseable one-event-at-a-time replay with explicit state transitions."""

    def __init__(
        self,
        bars: Sequence[MarketBar],
        *,
        strategy: Strategy,
        policy: RiskPolicy,
        backtest_config: BacktestConfig,
        replay_config: PaperReplayConfig | None = None,
        metadata: MarketDataMetadata | None = None,
        warnings: Sequence[DataWarning] = (),
        event_sink: EventSink | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bars:
            raise ValueError("paper replay requires at least one bar")
        self._bars = tuple(bars)
        self._cursor = 0
        self._status = PaperSessionStatus.CREATED
        self._transitions: list[SessionTransition] = []
        self._replay_config = replay_config or PaperReplayConfig()
        self._event_sink = event_sink
        self._sleeper = sleeper
        self._engine = SimulationEngine(
            strategy=strategy,
            policy=policy,
            config=backtest_config,
            metadata=metadata,
            warnings=warnings,
            event_sink=event_sink,
        )

    @property
    def status(self) -> PaperSessionStatus:
        return self._status

    @property
    def bars_processed(self) -> int:
        return self._cursor

    @property
    def current_timestamp(self) -> datetime | None:
        if self._cursor == 0:
            return None
        return self._bars[self._cursor - 1].timestamp

    def start(self) -> None:
        if self._status is not PaperSessionStatus.CREATED:
            raise RuntimeError("only a created paper session can start")
        self._transition(PaperSessionStatus.RUNNING, "session started")

    def pause(self) -> None:
        if self._status is not PaperSessionStatus.RUNNING:
            raise RuntimeError("only a running paper session can pause")
        self._transition(PaperSessionStatus.PAUSED, "manual pause")

    def resume(self) -> None:
        if self._status is not PaperSessionStatus.PAUSED:
            raise RuntimeError("only a paused paper session can resume")
        self._transition(PaperSessionStatus.RUNNING, "manual resume")

    def stop(self) -> None:
        if self._status not in {PaperSessionStatus.RUNNING, PaperSessionStatus.PAUSED}:
            raise RuntimeError("only a running or paused paper session can stop")
        self._transition(PaperSessionStatus.STOPPED, "manual stop")

    def activate_kill_switch(self) -> None:
        if self._status not in {PaperSessionStatus.RUNNING, PaperSessionStatus.PAUSED}:
            raise RuntimeError("kill switch requires a running or paused session")
        self._engine.activate_kill_switch(self._transition_timestamp())
        self._transition(PaperSessionStatus.HALTED, "manual kill switch")

    def step(self) -> None:
        """Deliver one bar; the strategy receives only the processed prefix."""

        if self._status is not PaperSessionStatus.RUNNING:
            raise RuntimeError("paper session must be running to process a bar")
        if self._cursor >= len(self._bars):
            self._transition(PaperSessionStatus.COMPLETED, "all bars processed")
            return
        self._engine.process_bar(self._bars[self._cursor])
        self._cursor += 1
        if self._engine.halted:
            self._transition(PaperSessionStatus.HALTED, "risk circuit breaker")
        elif self._cursor == len(self._bars):
            self._transition(PaperSessionStatus.COMPLETED, "all bars processed")

    def run_to_completion(self) -> PaperSessionSummary:
        """Run from created/paused state until completion or halt."""

        if self._status is PaperSessionStatus.CREATED:
            self.start()
        elif self._status is PaperSessionStatus.PAUSED:
            self.resume()
        while self._status is PaperSessionStatus.RUNNING:
            self.step()
            if (
                self._status is PaperSessionStatus.RUNNING
                and self._replay_config.replay_speed_seconds > 0
            ):
                self._sleeper(self._replay_config.replay_speed_seconds)
        return self.summary()

    def summary(self) -> PaperSessionSummary:
        if self._cursor == 0:
            raise RuntimeError("paper session summary requires at least one processed bar")
        return PaperSessionSummary(
            session_id=self._engine.session_id,
            status=self._status,
            bars_processed=self._cursor,
            total_bars=len(self._bars),
            replay_speed_seconds=self._replay_config.replay_speed_seconds,
            strategy_name=self._engine.strategy.name,
            engine_version=ENGINE_VERSION,
            transitions=tuple(self._transitions),
            result=self._engine.finish(),
        )

    def _transition(self, target: PaperSessionStatus, reason: str) -> None:
        transition = SessionTransition(
            timestamp=self._transition_timestamp(),
            from_status=self._status,
            to_status=target,
            reason=reason,
        )
        self._transitions.append(transition)
        self._status = target
        if self._event_sink is not None:
            self._event_sink(
                {
                    "event": "paper_session_transition",
                    "session_id": self._engine.session_id,
                    "strategy_name": self._engine.strategy.name,
                    "symbol": self._bars[0].symbol,
                    "event_timestamp": transition.timestamp.isoformat(),
                    "from_status": transition.from_status.value,
                    "to_status": transition.to_status.value,
                    "reason": reason,
                }
            )

    def _transition_timestamp(self) -> datetime:
        if self._cursor > 0:
            return self._bars[self._cursor - 1].timestamp
        return self._bars[0].timestamp


def export_paper_session_json(summary: PaperSessionSummary, path: str | Path) -> Path:
    """Write a local versioned paper-session summary."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "mode": "historical_paper_replay",
        "session_id": summary.session_id,
        "status": summary.status,
        "bars_processed": summary.bars_processed,
        "total_bars": summary.total_bars,
        "replay_speed_seconds": summary.replay_speed_seconds,
        "strategy_name": summary.strategy_name,
        "engine_version": summary.engine_version,
        "transitions": [asdict(transition) for transition in summary.transitions],
        "result_summary": asdict(summary.result.summary),
        "halt_state": asdict(summary.result.halt_state),
        "disclaimer": (
            "Local historical replay only; no external API or real order execution is present."
        ),
    }
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    return output


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = [
    "HistoricalReplaySession",
    "PaperReplayConfig",
    "export_paper_session_json",
]
