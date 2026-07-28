"""Entirely local historical replay for simulated paper-trading research."""

from __future__ import annotations

import json
import platform
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from pathlib import Path

from trading_bot_lab import __version__
from trading_bot_lab.artifacts import artifact_filename, atomic_write_text
from trading_bot_lab.backtesting.engine import (
    ENGINE_VERSION,
    EVENT_SCHEMA_VERSION,
    BacktestConfig,
    EventSink,
    SimulationEngine,
    build_market_data_metadata,
    validate_simulation_bars,
)
from trading_bot_lab.domain import (
    BacktestResult,
    DataWarning,
    MarketBar,
    MarketDataMetadata,
    PaperSessionStatus,
    PaperSessionSummary,
    SessionStateError,
    SessionTransition,
    Strategy,
)
from trading_bot_lab.risk import RiskPolicy

PAPER_REPORT_SCHEMA_VERSION = "1.2.0"
PAPER_DISCLAIMER = (
    "Hypothetical local historical replay for research only; no external API, live trading, "
    "or real order execution is present. Results are not financial advice, a profitability "
    "claim, or evidence of future performance."
)

_LEGAL_TRANSITIONS: dict[PaperSessionStatus, frozenset[PaperSessionStatus]] = {
    PaperSessionStatus.CREATED: frozenset(
        {PaperSessionStatus.VALIDATED, PaperSessionStatus.FAILED}
    ),
    PaperSessionStatus.VALIDATED: frozenset(
        {
            PaperSessionStatus.RUNNING,
            PaperSessionStatus.STOPPED,
            PaperSessionStatus.HALTED,
            PaperSessionStatus.FAILED,
        }
    ),
    PaperSessionStatus.RUNNING: frozenset(
        {
            PaperSessionStatus.PAUSED,
            PaperSessionStatus.STOPPED,
            PaperSessionStatus.COMPLETED,
            PaperSessionStatus.HALTED,
            PaperSessionStatus.FAILED,
        }
    ),
    PaperSessionStatus.PAUSED: frozenset(
        {
            PaperSessionStatus.RUNNING,
            PaperSessionStatus.STOPPED,
            PaperSessionStatus.HALTED,
            PaperSessionStatus.FAILED,
        }
    ),
    PaperSessionStatus.STOPPED: frozenset(),
    PaperSessionStatus.COMPLETED: frozenset(),
    PaperSessionStatus.HALTED: frozenset(),
    PaperSessionStatus.FAILED: frozenset(),
}

_TRANSITION_EVENTS = {
    PaperSessionStatus.VALIDATED: "data_validated",
    PaperSessionStatus.RUNNING: "session_started",
    PaperSessionStatus.PAUSED: "session_paused",
    PaperSessionStatus.STOPPED: "session_stopped",
    PaperSessionStatus.COMPLETED: "session_completed",
    PaperSessionStatus.HALTED: "kill_switch_activated",
    PaperSessionStatus.FAILED: "session_failed",
}


@dataclass(frozen=True)
class PaperReplayConfig:
    """Deterministic replay scheduling and manifest configuration."""

    replay_speed_seconds: float = 0.0
    random_seed: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.replay_speed_seconds, bool)
            or not isinstance(self.replay_speed_seconds, (int, float))
            or not isfinite(self.replay_speed_seconds)
            or not 0 <= self.replay_speed_seconds <= 60
        ):
            raise ValueError("replay_speed_seconds must be finite and between 0 and 60")
        if type(self.random_seed) is not int or not 0 <= self.random_seed <= 2**32 - 1:
            raise ValueError("random_seed must be an integer between 0 and 4294967295")


class HistoricalReplaySession:
    """Pauseable one-event-at-a-time replay with an explicit typed state machine."""

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
        self._bars = validate_simulation_bars(bars, metadata=metadata)
        resolved_metadata = metadata or build_market_data_metadata(self._bars)
        self._cursor = 0
        self._status = PaperSessionStatus.CREATED
        self._transitions: list[SessionTransition] = []
        self._failure_reason: str | None = None
        self._replay_config = replay_config or PaperReplayConfig()
        self._sleeper = sleeper
        self._engine = SimulationEngine(
            strategy=strategy,
            policy=policy,
            config=backtest_config,
            metadata=resolved_metadata,
            warnings=warnings,
            validated_bars=self._bars,
            event_sink=event_sink,
        )
        self._publish_lifecycle_event("session_created", self._bars[0].timestamp)
        self._transition(PaperSessionStatus.VALIDATED, "validated input data")

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
        self._require_status(PaperSessionStatus.VALIDATED, operation="start")
        self._transition(PaperSessionStatus.RUNNING, "session started")

    def pause(self) -> None:
        self._require_status(PaperSessionStatus.RUNNING, operation="pause")
        self._transition(PaperSessionStatus.PAUSED, "manual pause")

    def resume(self) -> None:
        self._require_status(PaperSessionStatus.PAUSED, operation="resume")
        self._transition(PaperSessionStatus.RUNNING, "manual resume")

    def stop(self) -> None:
        if self._status is PaperSessionStatus.STOPPED:
            return
        if self._status not in {
            PaperSessionStatus.VALIDATED,
            PaperSessionStatus.RUNNING,
            PaperSessionStatus.PAUSED,
        }:
            raise SessionStateError(f"cannot stop a {self._status.value} paper session")
        self._engine.expire_pending_signal(self._transition_timestamp(), "manual stop")
        self._transition(PaperSessionStatus.STOPPED, "manual stop")

    def activate_kill_switch(self) -> None:
        if self._status is PaperSessionStatus.HALTED:
            return
        if self._status not in {
            PaperSessionStatus.VALIDATED,
            PaperSessionStatus.RUNNING,
            PaperSessionStatus.PAUSED,
        }:
            raise SessionStateError(
                f"cannot activate kill switch for a {self._status.value} paper session"
            )
        self._engine.activate_kill_switch(self._transition_timestamp())
        self._transition(PaperSessionStatus.HALTED, "manual kill switch")

    def step(self) -> None:
        """Deliver one bar; strategy code receives only bounded trailing history."""

        self._require_status(PaperSessionStatus.RUNNING, operation="process a bar")
        if self._cursor >= len(self._bars):
            self._complete()
            return
        bar = self._bars[self._cursor]
        try:
            self._engine.process_bar(bar)
        except Exception as exc:
            self._failure_reason = f"bar_processing_failed:{type(exc).__name__}"
            self._transition(
                PaperSessionStatus.FAILED,
                self._failure_reason,
                timestamp=bar.timestamp,
            )
            raise
        self._cursor += 1
        if self._engine.halted:
            self._engine.expire_pending_signal(bar.timestamp, "risk circuit breaker")
            self._transition(PaperSessionStatus.HALTED, "risk circuit breaker")
        elif self._cursor == len(self._bars):
            self._complete()

    def run_to_completion(self) -> PaperSessionSummary:
        """Run from validated or paused state until completion or halt."""

        try:
            if self._status is PaperSessionStatus.VALIDATED:
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
        except Exception as exc:
            if self._status in {
                PaperSessionStatus.VALIDATED,
                PaperSessionStatus.RUNNING,
                PaperSessionStatus.PAUSED,
            }:
                self.fail_runtime(exc)
            raise
        return self.summary()

    def fail_runtime(self, error: Exception) -> None:
        """Terminalize a non-bar replay failure without changing committed bar state."""

        if self._status is PaperSessionStatus.FAILED:
            return
        if self._status not in {
            PaperSessionStatus.VALIDATED,
            PaperSessionStatus.RUNNING,
            PaperSessionStatus.PAUSED,
        }:
            raise SessionStateError(
                f"cannot fail a {self._status.value} paper session during replay runtime"
            )
        self._failure_reason = f"replay_runtime_failed:{type(error).__name__}"
        self._engine.expire_pending_signal(
            self._transition_timestamp(),
            "replay runtime failure",
        )
        self._transition(PaperSessionStatus.FAILED, self._failure_reason)

    def summary(self) -> PaperSessionSummary:
        result = self._engine.finish() if self._cursor > 0 else None
        return PaperSessionSummary(
            session_id=self._engine.session_id,
            status=self._status,
            bars_processed=self._cursor,
            total_bars=len(self._bars),
            replay_speed_seconds=self._replay_config.replay_speed_seconds,
            random_seed=self._replay_config.random_seed,
            strategy_name=self._engine.strategy_name,
            strategy_configuration=self._engine.strategy_configuration,
            engine_version=ENGINE_VERSION,
            input_metadata=self._engine.input_metadata,
            assumptions=self._engine.assumptions,
            risk_configuration=self._engine.risk_configuration,
            start_event_timestamp=self._bars[0].timestamp if self._cursor > 0 else None,
            end_event_timestamp=(
                self._bars[self._cursor - 1].timestamp if self._cursor > 0 else None
            ),
            halt_reasons=self._engine.halt_state.reasons,
            failure_reason=self._failure_reason,
            warnings=self._engine.warnings,
            transitions=tuple(self._transitions),
            result=result,
        )

    def _complete(self) -> None:
        self._engine.expire_pending_signal(self._transition_timestamp(), "replay completed")
        self._transition(PaperSessionStatus.COMPLETED, "all bars processed")

    def _require_status(self, expected: PaperSessionStatus, *, operation: str) -> None:
        if self._status is not expected:
            raise SessionStateError(
                f"paper session must be {expected.value} to {operation}; "
                f"current state is {self._status.value}"
            )

    def _transition(
        self,
        target: PaperSessionStatus,
        reason: str,
        *,
        timestamp: datetime | None = None,
    ) -> None:
        if target not in _LEGAL_TRANSITIONS[self._status]:
            raise SessionStateError(
                f"illegal paper-session transition {self._status.value}->{target.value}"
            )
        transition = SessionTransition(
            timestamp=timestamp or self._transition_timestamp(),
            from_status=self._status,
            to_status=target,
            reason=reason,
        )
        self._transitions.append(transition)
        self._status = target
        event_name = _TRANSITION_EVENTS[target]
        if target is PaperSessionStatus.HALTED and reason != "manual kill switch":
            event_name = "session_halted"
        elif (
            target is PaperSessionStatus.RUNNING
            and transition.from_status is PaperSessionStatus.PAUSED
        ):
            event_name = "session_resumed"
        self._publish_lifecycle_event(event_name, transition.timestamp, transition=transition)

    def _publish_lifecycle_event(
        self,
        event: str,
        timestamp: datetime,
        *,
        transition: SessionTransition | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event": event,
            "session_id": self._engine.session_id,
            "strategy_name": self._engine.strategy_name,
            "symbol": self._bars[0].symbol,
            "event_timestamp": timestamp.isoformat(),
            "halt_state": "halted" if self._engine.halted else "active",
        }
        if transition is not None:
            payload.update(
                {
                    "from_status": transition.from_status.value,
                    "to_status": transition.to_status.value,
                    "reason": transition.reason,
                }
            )
        self._engine.publish_event(payload)

    def _transition_timestamp(self) -> datetime:
        if self._cursor > 0:
            return self._bars[self._cursor - 1].timestamp
        return self._bars[0].timestamp


def export_paper_session_json(
    summary: PaperSessionSummary,
    path: str | Path,
    *,
    artifact_paths: Mapping[str, str | Path] | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Atomically write a reproducibility manifest and replay result summary."""

    output = Path(path)
    generated = generated_at or datetime.now(UTC)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    generated = generated.astimezone(UTC)
    filenames = {
        name: artifact_filename(value) for name, value in sorted((artifact_paths or {}).items())
    }
    filenames["manifest"] = artifact_filename(output)
    result = summary.result
    payload = {
        "schema_version": PAPER_REPORT_SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "historical_paper_replay",
        "session_id": summary.session_id,
        "final_state": summary.status,
        "status": summary.status,
        "bars_processed": summary.bars_processed,
        "total_bars": summary.total_bars,
        "replay_speed_seconds": summary.replay_speed_seconds,
        "random_seed": summary.random_seed,
        "strategy_name": summary.strategy_name,
        "strategy_configuration": dict(summary.strategy_configuration),
        "engine_version": summary.engine_version,
        "package_version": __version__,
        "python_version": platform.python_version(),
        "input_filename": summary.input_metadata.source,
        "input_content_sha256": summary.input_metadata.content_sha256,
        "normalized_bars_sha256": summary.input_metadata.bars_sha256,
        "input_data": asdict(summary.input_metadata),
        "backtest_assumptions": asdict(summary.assumptions),
        "risk_configuration": asdict(summary.risk_configuration),
        "execution_timing": summary.assumptions.execution_timing,
        "start_event_timestamp": summary.start_event_timestamp,
        "end_event_timestamp": summary.end_event_timestamp,
        "failure_reason": summary.failure_reason,
        "halt_reasons": [reason.value for reason in summary.halt_reasons],
        "artifact_filenames": filenames,
        "data_validation_warnings": [
            asdict(warning) for warning in summary.warnings if warning.is_data_validation
        ],
        "simulation_warnings": [
            asdict(warning) for warning in summary.warnings if not warning.is_data_validation
        ],
        "transitions": [asdict(transition) for transition in summary.transitions],
        "result_summary": asdict(result.summary) if result is not None else None,
        "benchmark_summaries": (
            {
                "buy_and_hold": asdict(result.benchmarks.buy_and_hold),
                "cash": asdict(result.benchmarks.cash),
            }
            if result is not None
            else None
        ),
        "rejection_summary": _rejection_summary(result) if result is not None else None,
        "halt_state": asdict(result.halt_state) if result is not None else None,
        "disclaimer": PAPER_DISCLAIMER,
    }
    serialized = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        default=_json_default,
        allow_nan=False,
    )
    return atomic_write_text(output, serialized)


def _rejection_summary(result: BacktestResult) -> dict[str, object]:
    decisions = result.risk_decisions
    counts: dict[str, int] = {}
    rejected_count = 0
    for decision in decisions:
        if decision.intent_id is None or decision.status.value != "rejected":
            continue
        rejected_count += 1
        for reason in decision.reasons:
            counts[reason.value] = counts.get(reason.value, 0) + 1
    return {"rejected_intent_count": rejected_count, "counts_by_reason": counts}


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = [
    "HistoricalReplaySession",
    "PAPER_REPORT_SCHEMA_VERSION",
    "PaperReplayConfig",
    "export_paper_session_json",
]
