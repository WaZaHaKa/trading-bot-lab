from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import trading_bot_lab.artifacts as artifacts_module
from tests.support import TargetSequenceStrategy, make_bars
from trading_bot_lab.backtesting import (
    BacktestConfig,
    MovingAverageStrategy,
    NoTradeStrategy,
    SimulationEngine,
    build_market_data_metadata,
    export_equity_csv,
    export_json_report,
    export_risk_events_csv,
    export_trades_csv,
    load_market_data_csv,
    run_backtest,
    run_moving_average_backtest,
)
from trading_bot_lab.cli import _validate_selected_artifact_paths, main
from trading_bot_lab.domain import (
    DomainValidationError,
    MarketBar,
    PaperSessionStatus,
    RiskReason,
    SessionStateError,
    Signal,
    WarningCode,
)
from trading_bot_lab.observability import DEFAULT_LOG_MAX_BYTES, StructuredEventSink
from trading_bot_lab.paper import (
    HistoricalReplaySession,
    PaperReplayConfig,
    export_paper_session_json,
)
from trading_bot_lab.provenance import safe_source_filename
from trading_bot_lab.risk import RiskPolicy


def _policy() -> RiskPolicy:
    return RiskPolicy(
        allowed_symbols=("SPY",),
        max_daily_loss_pct=1.0,
        max_drawdown_pct=1.0,
    )


def _config(**overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "max_daily_loss_pct": 1.0,
        "max_drawdown_pct": 1.0,
    }
    values.update(overrides)
    return BacktestConfig(**values)


def _write_csv(path: Path, closes: tuple[int, ...]) -> Path:
    rows = ["date,symbol,open,high,low,close,volume"]
    for index, close in enumerate(closes, start=1):
        rows.append(f"2024-01-{index:02d},SPY,{close},{close + 1},{close - 1},{close},1000")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _structured_bar_event(index: int) -> dict[str, object]:
    return {
        "event_schema_version": "1.0.0",
        "event": "bar_received",
        "session_id": "sim-rotation-test",
        "strategy_name": "no_trade",
        "symbol": "SPY",
        "event_timestamp": f"2024-01-{index + 1:02d}T00:00:00+00:00",
        "close": 100.0 + index,
    }


def _artifact_args(**overrides: Path) -> argparse.Namespace:
    values: dict[str, Path | None] = {
        "export_json": None,
        "export_equity_csv": None,
        "export_trades_csv": None,
        "export_rejections_csv": None,
        "export_risk_events_csv": None,
        "log_jsonl": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_input_hash_is_exact_path_safe_and_session_identity_is_content_bound(
    tmp_path: Path,
) -> None:
    first_path = _write_csv(tmp_path / "first.csv", (100, 101))
    second_path = tmp_path / "renamed.csv"
    second_path.write_bytes(first_path.read_bytes())
    changed_path = _write_csv(tmp_path / "changed.csv", (100, 102))

    first = load_market_data_csv(first_path)
    second = load_market_data_csv(second_path)
    changed = load_market_data_csv(changed_path)
    first_result = run_backtest(
        first.bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        metadata=first.metadata,
    )
    second_result = run_backtest(
        second.bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        metadata=second.metadata,
    )
    changed_result = run_backtest(
        changed.bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        metadata=changed.metadata,
    )

    assert first.metadata.source == "first.csv"
    assert len(first.metadata.content_sha256) == 64
    assert first.metadata.content_sha256 == second.metadata.content_sha256
    assert first_result.session_id == second_result.session_id
    assert changed.metadata.content_sha256 != first.metadata.content_sha256
    assert changed_result.session_id != first_result.session_id


def test_stale_metadata_cannot_be_reused_for_different_normalized_bars() -> None:
    original = make_bars([100, 101])
    changed = make_bars([100, 999])
    metadata = build_market_data_metadata(original)

    with pytest.raises(DomainValidationError, match="normalized-bar hash"):
        run_backtest(
            changed,
            strategy=NoTradeStrategy(),
            policy=_policy(),
            config=_config(),
            metadata=metadata,
        )


def test_direct_engine_results_are_content_bound() -> None:
    first_bars = make_bars([100, 101])
    second_bars = make_bars([100, 999])
    first = SimulationEngine(
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        validated_bars=first_bars,
    )
    second = SimulationEngine(
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        validated_bars=second_bars,
    )
    for bar in first_bars:
        first.process_bar(bar)
    for bar in second_bars:
        second.process_bar(bar)

    assert first.finish().session_id != second.finish().session_id


def test_engine_rejects_events_that_differ_from_validated_sequence() -> None:
    validated = make_bars([100, 101])
    changed = make_bars([100, 999])
    engine = SimulationEngine(
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        validated_bars=validated,
    )
    engine.process_bar(validated[0])

    with pytest.raises(DomainValidationError, match="validated input event sequence"):
        engine.process_bar(changed[1])


def test_report_provenance_never_serializes_the_absolute_input_path(tmp_path: Path) -> None:
    dataset = load_market_data_csv(_write_csv(tmp_path / "private-bars.csv", (100, 101)))
    result = run_backtest(
        dataset.bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        metadata=dataset.metadata,
    )
    report = export_json_report(
        result,
        _config(),
        tmp_path / "report.json",
        generated_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    serialized = report.read_text(encoding="utf-8")

    assert str(tmp_path).replace("\\", "/") not in serialized.replace("\\", "/")
    payload = json.loads(serialized)
    assert payload["input_filename"] == "private-bars.csv"
    assert payload["input_content_sha256"] == dataset.metadata.content_sha256


def test_buy_and_hold_applies_warmup_costs_precision_and_keeps_position_open() -> None:
    selected = _config(
        initial_cash=1_000,
        warmup_bars=1,
        fee_bps=100,
        minimum_fee=5,
        slippage_bps=100,
        quantity_precision=0,
    )
    result = run_backtest(
        make_bars([10, 21, 22], opens=[10, 20, 22]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=selected,
    )
    benchmark = result.benchmarks.buy_and_hold
    cash = result.benchmarks.cash

    assert benchmark.purchase_timestamp == make_bars([10, 21, 22])[1].timestamp
    assert benchmark.purchase_reference_price == 20
    assert benchmark.purchase_execution_price == pytest.approx(20.2)
    assert benchmark.quantity == 49
    assert benchmark.total_fees_paid == pytest.approx(9.898)
    assert benchmark.estimated_slippage_cost == pytest.approx(9.8)
    assert benchmark.ending_equity == pytest.approx(1_078.302)
    assert benchmark.ending_position_open is True
    assert benchmark.fractional_quantity_supported is False
    assert 0 < benchmark.average_exposure <= benchmark.max_exposure < 1
    assert "no final sale" in benchmark.methodology
    assert cash.ending_equity == cash.starting_cash == 1_000
    assert cash.total_fees_paid == cash.estimated_slippage_cost == 0
    assert cash.average_exposure == cash.max_exposure == cash.max_drawdown == 0


def test_buy_and_hold_remains_cash_when_warmup_consumes_all_bars() -> None:
    result = run_backtest(
        make_bars([100, 110]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(warmup_bars=2),
    )

    benchmark = result.benchmarks.buy_and_hold
    assert benchmark.ending_equity == benchmark.starting_cash
    assert benchmark.quantity == 0
    assert benchmark.purchase_timestamp is None
    assert benchmark.ending_position_open is False


def test_trade_and_risk_csvs_expose_auditable_fields(tmp_path: Path) -> None:
    result = run_backtest(
        make_bars([100, 100, 105]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=_policy(),
        config=_config(),
    )
    trades = list(
        csv.DictReader(export_trades_csv(result, tmp_path / "trades.csv").open(encoding="utf-8"))
    )
    risk_events = list(
        csv.DictReader(export_risk_events_csv(result, tmp_path / "risk.csv").open(encoding="utf-8"))
    )

    assert trades
    assert {
        "intent_timestamp",
        "fill_timestamp",
        "fill_id",
        "fill_price",
        "resulting_cash",
        "resulting_quantity",
    } <= trades[0].keys()
    assert trades[0]["intent_timestamp"] < trades[0]["fill_timestamp"]
    assert trades[0]["fill_id"].startswith("fill-")
    assert risk_events
    assert json.loads(risk_events[0]["metrics"])


def test_fill_ids_are_namespaced_by_content_bound_session() -> None:
    first = run_backtest(
        make_bars([100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1)),
        policy=_policy(),
        config=_config(),
    )
    second = run_backtest(
        make_bars([100, 101]),
        strategy=TargetSequenceStrategy((0.1, 0.1)),
        policy=_policy(),
        config=_config(),
    )

    assert first.session_id != second.session_id
    assert first.fills[0].fill_id != second.fills[0].fill_id


def test_atomic_report_failure_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "equity.csv"
    destination.write_text("known-good\n", encoding="utf-8")
    result = run_backtest(
        make_bars([100]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
    )

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(artifacts_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        export_equity_csv(result, destination)

    assert destination.read_text(encoding="utf-8") == "known-good\n"
    assert list(tmp_path.glob(".equity.csv.*.tmp")) == []


def test_exports_are_byte_stable_when_generation_timestamp_is_controlled(
    tmp_path: Path,
) -> None:
    result = run_backtest(
        make_bars([100, 101]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
    )
    timestamp = datetime(2026, 7, 18, 12, tzinfo=UTC)
    first = export_json_report(
        result,
        _config(),
        tmp_path / "first.json",
        generated_at=timestamp,
    )
    second = export_json_report(
        result,
        _config(),
        tmp_path / "second.json",
        generated_at=timestamp,
    )

    assert first.read_bytes() == second.read_bytes()


def test_zero_bar_stop_and_kill_are_summarizable_and_idempotent() -> None:
    stopped = HistoricalReplaySession(
        make_bars([100, 101]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
    )
    assert stopped.status is PaperSessionStatus.VALIDATED
    stopped.stop()
    stopped.stop()
    stopped_summary = stopped.summary()
    assert stopped_summary.status is PaperSessionStatus.STOPPED
    assert stopped_summary.bars_processed == 0
    assert stopped_summary.result is None

    killed = HistoricalReplaySession(
        make_bars([100, 101]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
    )
    killed.activate_kill_switch()
    killed.activate_kill_switch()
    killed_summary = killed.summary()
    assert killed_summary.status is PaperSessionStatus.HALTED
    assert killed_summary.result is None
    assert killed_summary.halt_reasons == (RiskReason.KILL_SWITCH,)
    with pytest.raises(SessionStateError, match="must be validated"):
        killed.start()


def test_first_bar_failure_records_typed_terminal_reason() -> None:
    class FailingStrategy:
        name = "failing_strategy"

        def signal_for_history(self, _history: object) -> Signal:
            raise LookupError("synthetic strategy failure")

    session = HistoricalReplaySession(
        make_bars([100]),
        strategy=FailingStrategy(),
        policy=_policy(),
        backtest_config=_config(),
    )
    session.start()
    with pytest.raises(LookupError, match="synthetic strategy failure"):
        session.step()

    summary = session.summary()
    assert summary.status is PaperSessionStatus.FAILED
    assert summary.bars_processed == 0
    assert summary.result is None
    assert summary.failure_reason == "bar_processing_failed:LookupError"


def test_failed_strategy_cannot_publish_a_phantom_transactional_event() -> None:
    class PublishingFailureStrategy:
        name = "publishing_failure"
        engine: SimulationEngine | None = None

        def signal_for_history(self, _history: object) -> Signal:
            assert self.engine is not None
            self.engine.publish_event({"event": "phantom"})
            raise LookupError("synthetic failure")

    events: list[dict[str, object]] = []
    strategy = PublishingFailureStrategy()
    bars = make_bars([100])
    engine = SimulationEngine(
        strategy=strategy,
        policy=_policy(),
        config=_config(),
        validated_bars=bars,
        event_sink=events.append,
    )
    strategy.engine = engine

    with pytest.raises(RuntimeError, match="mutate protected engine state"):
        engine.process_bar(bars[0])

    assert events == []


def test_event_sink_cannot_mutate_committed_financial_state() -> None:
    engine: SimulationEngine
    bars = make_bars([100])

    def mutating_sink(_event: dict[str, object]) -> None:
        engine._cash = 1

    engine = SimulationEngine(
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        validated_bars=bars,
        event_sink=mutating_sink,
    )
    point = engine.process_bar(bars[0])
    result = engine.finish()

    assert point.cash == engine.portfolio_state.cash == 100_000
    assert result.summary.ending_equity == 100_000
    assert [warning.code for warning in result.warnings] == [WarningCode.EVENT_SINK_FAILURE]
    assert "mutated protected engine state" in result.warnings[0].message


def test_event_sink_invalid_container_mutation_is_rolled_back() -> None:
    engine: SimulationEngine
    bars = make_bars([100])

    def corrupting_sink(_event: dict[str, object]) -> None:
        engine._risk_decisions = None

    engine = SimulationEngine(
        strategy=NoTradeStrategy(),
        policy=_policy(),
        config=_config(),
        validated_bars=bars,
        event_sink=corrupting_sink,
    )

    point = engine.process_bar(bars[0])
    result = engine.finish()

    assert point.cash == result.summary.ending_equity == 100_000
    assert result.risk_decisions == ()
    assert result.warnings[0].code is WarningCode.EVENT_SINK_FAILURE
    assert "state invalid" in result.warnings[0].message


def test_closed_structured_sink_rejects_silent_event_loss(tmp_path: Path) -> None:
    sink = StructuredEventSink(tmp_path / "events.jsonl")
    assert sink.close() is None

    with pytest.raises(RuntimeError, match="sink is closed"):
        sink({"event": "session_completed"})


def test_structured_sink_rejects_unknown_event_names(tmp_path: Path) -> None:
    sink = StructuredEventSink(tmp_path / "events.jsonl")
    try:
        with pytest.raises(ValueError, match="event name"):
            sink(
                {
                    "event_schema_version": "1.0.0",
                    "event": "arbitrary_object_dump",
                    "session_id": "sim-test",
                    "strategy_name": "test",
                    "symbol": "SPY",
                    "event_timestamp": "2024-01-01T00:00:00+00:00",
                }
            )
    finally:
        sink.close()


def test_structured_sink_requires_at_least_one_rotation_backup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="backup_count must be a positive integer"):
        StructuredEventSink(tmp_path / "events.jsonl", max_bytes=400, backup_count=0)


def test_structured_sink_rotation_keeps_each_retained_file_bounded(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    max_bytes = 400
    sink = StructuredEventSink(path, max_bytes=max_bytes, backup_count=1)
    try:
        for index in range(10):
            sink(_structured_bar_event(index))
    finally:
        sink.close()

    backup = Path(f"{path}.1")
    assert sink.artifact_paths == (path, backup)
    assert path.exists() and backup.exists()
    assert 0 < path.stat().st_size < max_bytes
    assert 0 < backup.stat().st_size < max_bytes
    assert not Path(f"{path}.2").exists()


def test_strategy_history_is_bounded_without_future_rows() -> None:
    strategy = TargetSequenceStrategy((0.0, 0.0, 0.0, 0.0))
    session = HistoricalReplaySession(
        make_bars([100, 101, 102, 103]),
        strategy=strategy,
        policy=_policy(),
        backtest_config=_config(strategy_history_limit=2),
    )

    session.run_to_completion()

    assert strategy.history_lengths == [1, 2, 2, 2]


def test_builtin_moving_average_rejects_an_unreachable_history_window() -> None:
    bars = make_bars([100, 101, 102, 103])
    strategy = MovingAverageStrategy(fast_window=2, slow_window=3)
    selected = _config(strategy_history_limit=2)

    with pytest.raises(ValueError, match="strategy_history_limit"):
        run_moving_average_backtest(
            bars,
            strategy=strategy,
            policy=_policy(),
            config=selected,
        )

    with pytest.raises(ValueError, match="strategy_history_limit"):
        HistoricalReplaySession(
            bars,
            strategy=strategy,
            policy=_policy(),
            backtest_config=selected,
        )


def test_replay_speed_changes_scheduling_but_not_financial_results() -> None:
    bars = make_bars([100, 101, 102])
    sleep_calls: list[float] = []
    fast = HistoricalReplaySession(
        bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
        replay_config=PaperReplayConfig(0),
        sleeper=lambda _seconds: pytest.fail("zero-delay replay must not sleep"),
    ).run_to_completion()
    scheduled = HistoricalReplaySession(
        bars,
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
        replay_config=PaperReplayConfig(0.25),
        sleeper=sleep_calls.append,
    ).run_to_completion()

    assert fast.result == scheduled.result
    assert fast.session_id == scheduled.session_id
    assert sleep_calls == [0.25, 0.25]


def test_replay_logs_complete_lifecycle_and_processing_taxonomy() -> None:
    events: list[dict[str, object]] = []
    session = HistoricalReplaySession(
        make_bars([100, 101, 102]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=_policy(),
        backtest_config=_config(),
        event_sink=events.append,
    )
    session.run_to_completion()

    names = {event["event"] for event in events}
    assert {
        "session_created",
        "data_validated",
        "session_started",
        "bar_received",
        "signal_generated",
        "intent_created",
        "risk_accepted",
        "fill_created",
        "portfolio_updated",
        "pending_signal_expired",
        "session_completed",
    } <= names
    assert all(event["event_schema_version"] == "1.0.0" for event in events)
    assert all("session_id" in event and "event_timestamp" in event for event in events)


def test_risk_halt_logs_terminal_state_and_pending_signal_expiry() -> None:
    events: list[dict[str, object]] = []
    session = HistoricalReplaySession(
        make_bars([100, 100, 50], opens=[100, 100, 50]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=1.0,
            max_drawdown_pct=0.01,
        ),
        backtest_config=_config(max_drawdown_pct=0.01),
        event_sink=events.append,
    )

    summary = session.run_to_completion()
    names = {event["event"] for event in events}

    assert summary.status is PaperSessionStatus.HALTED
    assert "session_halted" in names
    assert "pending_signal_expired" in names


def test_manifest_is_path_safe_complete_and_byte_stable(tmp_path: Path) -> None:
    summary = HistoricalReplaySession(
        make_bars([100, 101]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
        replay_config=PaperReplayConfig(random_seed=7),
    ).run_to_completion()
    generated = datetime(2026, 7, 18, 12, tzinfo=UTC)
    artifacts = {
        "event_log": tmp_path / "session.jsonl",
        "equity_csv": tmp_path / "equity.csv",
    }
    first = export_paper_session_json(
        summary,
        tmp_path / "first.json",
        artifact_paths=artifacts,
        generated_at=generated,
    )
    first_bytes = first.read_bytes()
    second = export_paper_session_json(
        summary,
        tmp_path / "first.json",
        artifact_paths=artifacts,
        generated_at=generated,
    )
    payload = json.loads(first.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.2.0"
    assert payload["random_seed"] == 7
    assert payload["input_content_sha256"] == summary.input_metadata.content_sha256
    assert payload["execution_timing"] == "next_bar_open"
    assert payload["python_version"]
    assert payload["package_version"]
    assert payload["final_state"] == "completed"
    assert payload["artifact_filenames"] == {
        "equity_csv": "equity.csv",
        "event_log": "session.jsonl",
        "manifest": "first.json",
    }
    assert str(tmp_path).replace("\\", "/") not in first.read_text(encoding="utf-8").replace(
        "\\", "/"
    )
    assert first_bytes == second.read_bytes()


def test_manifest_rejects_naive_generation_timestamp(tmp_path: Path) -> None:
    summary = HistoricalReplaySession(
        make_bars([100]),
        strategy=NoTradeStrategy(),
        policy=_policy(),
        backtest_config=_config(),
    ).run_to_completion()

    with pytest.raises(ValueError, match="include a timezone"):
        export_paper_session_json(
            summary,
            tmp_path / "manifest.json",
            generated_at=datetime(2026, 7, 18),
        )


def test_cli_exports_all_replay_artifacts_and_rejects_trackable_repo_paths(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    equity = tmp_path / "equity.csv"
    trades = tmp_path / "trades.csv"
    rejections = tmp_path / "rejections.csv"
    risk_events = tmp_path / "risk.csv"
    log = tmp_path / "session.jsonl"

    assert (
        main(
            [
                "paper-replay",
                "--speed",
                "0",
                "--random-seed",
                "11",
                "--export-manifest",
                str(manifest),
                "--export-equity-csv",
                str(equity),
                "--export-trades-csv",
                str(trades),
                "--export-rejections-csv",
                str(rejections),
                "--export-risk-events-csv",
                str(risk_events),
                "--log-jsonl",
                str(log),
            ]
        )
        == 0
    )
    assert all(path.exists() for path in (manifest, equity, trades, rejections, risk_events, log))
    assert main(["backtest", "--export-json", "trackable-report.json"]) == 2


def test_cli_rejects_colliding_artifacts_and_unreachable_controls(tmp_path: Path) -> None:
    collision = tmp_path / "collision.out"

    assert (
        main(
            [
                "backtest",
                "--export-json",
                str(collision),
                "--export-equity-csv",
                str(collision),
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "paper-replay",
                "--kill-switch-after-bars",
                "1",
                "--stop-after-bars",
                "2",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "paper-replay",
                "--pause-after-bars",
                "2",
                "--stop-after-bars",
                "1",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "paper-replay",
                "--stop-after-bars",
                "0",
                "--export-equity-csv",
                str(tmp_path / "unavailable.csv"),
            ]
        )
        == 2
    )


def test_cli_collision_guard_rejects_a_hardlink_alias_to_input(tmp_path: Path) -> None:
    input_path = _write_csv(tmp_path / "input.csv", (100, 101))
    original = input_path.read_bytes()
    report_alias = tmp_path / "report.json"
    try:
        report_alias.hardlink_to(input_path)
    except OSError as error:
        pytest.skip(f"hard links are unavailable: {type(error).__name__}")
    try:
        with pytest.raises(ValueError, match="input CSV"):
            _validate_selected_artifact_paths(
                _artifact_args(export_json=report_alias),
                data_path=input_path,
            )
        assert input_path.read_bytes() == original
    finally:
        report_alias.unlink(missing_ok=True)


def test_cli_reserves_rotation_sidecars_before_a_presized_log_can_roll(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_bytes(b"x" * DEFAULT_LOG_MAX_BYTES)
    input_path = _write_csv(Path(f"{log_path}.1"), (100, 101))
    original_input = input_path.read_bytes()
    original_log = log_path.read_bytes()

    with pytest.raises(ValueError, match="input CSV"):
        _validate_selected_artifact_paths(
            _artifact_args(log_jsonl=log_path),
            data_path=input_path,
        )

    assert input_path.read_bytes() == original_input
    assert log_path.read_bytes() == original_log


def test_cli_rejects_a_report_at_a_reserved_rotation_sidecar(tmp_path: Path) -> None:
    input_path = _write_csv(tmp_path / "input.csv", (100, 101))
    log_path = tmp_path / "session.jsonl"
    report_path = Path(f"{log_path}.2")

    with pytest.raises(ValueError, match="different artifact paths"):
        _validate_selected_artifact_paths(
            _artifact_args(export_json=report_path, log_jsonl=log_path),
            data_path=input_path,
        )


def test_cli_pytest_scratch_exception_requires_a_lowercase_ignored_root() -> None:
    root = Path(__file__).resolve().parents[1]
    trackable = root / ".PYTEST-unignored" / "report.json"

    assert main(["backtest", "--export-json", str(trackable)]) == 2


def test_cli_surfaces_atomic_export_oserror_as_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(artifacts_module.os, "replace", fail_replace)

    assert main(["backtest", "--export-json", str(tmp_path / "report.json")]) == 2


def test_path_safe_basenames_handle_windows_drive_relative_inputs() -> None:
    metadata = build_market_data_metadata(make_bars([100]), source="private.csv")
    assert metadata.source == "private.csv"

    assert safe_source_filename("C:private.csv") == "private.csv"
    assert artifacts_module.artifact_filename("C:private.json") == "private.json"
    with pytest.raises(ValueError, match="safe filename"):
        artifacts_module.artifact_filename("")


def test_metadata_rejects_drive_relative_source_names() -> None:
    metadata = build_market_data_metadata(make_bars([100]))

    with pytest.raises(DomainValidationError, match="filename"):
        replace(metadata, source="C:private.csv")


@pytest.mark.parametrize("symbol", ("=2+2", "+SPY", "@SPY", "SPY/US"))
def test_symbols_that_can_inject_spreadsheet_formulas_are_rejected(symbol: str) -> None:
    with pytest.raises(DomainValidationError):
        MarketBar(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            symbol=symbol,
            open=100,
            high=101,
            low=99,
            close=100,
        )
