from __future__ import annotations

import csv
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.support import TargetSequenceStrategy, make_bars
from trading_bot_lab.backtesting import (
    BacktestConfig,
    MovingAverageStrategy,
    NoTradeStrategy,
    export_equity_csv,
    export_json_report,
    export_rejected_intents_csv,
    export_trades_csv,
    load_market_data_csv,
    run_backtest,
)
from trading_bot_lab.cli import main
from trading_bot_lab.domain import (
    DataWarning,
    MarketBar,
    PaperSessionStatus,
    Signal,
    WarningCode,
)
from trading_bot_lab.observability import StructuredEventSink
from trading_bot_lab.paper import (
    HistoricalReplaySession,
    PaperReplayConfig,
    export_paper_session_json,
)
from trading_bot_lab.risk import RiskPolicy


def policy() -> RiskPolicy:
    return RiskPolicy(
        allowed_symbols=("SPY",),
        max_daily_loss_pct=1.0,
        max_drawdown_pct=1.0,
    )


def config(**overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "max_daily_loss_pct": 1.0,
        "max_drawdown_pct": 1.0,
    }
    values.update(overrides)
    return BacktestConfig(**values)


def completed_result():
    return run_backtest(
        make_bars([100, 105, 110], opens=[100, 100, 110]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=config(fee_bps=1, slippage_bps=2),
    )


def test_json_report_has_stable_required_schema(tmp_path: Path) -> None:
    result = completed_result()
    path = export_json_report(
        result,
        config(fee_bps=1, slippage_bps=2),
        tmp_path / "summary.json",
        policy=policy(),
        warnings=("synthetic test fixture",),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    required = {
        "schema_version",
        "engine_version",
        "strategy_name",
        "input_data",
        "data_validation_warnings",
        "simulation_warnings",
        "backtest_assumptions",
        "risk_configuration",
        "result_summary",
        "benchmark_summaries",
        "halt_state",
        "rejection_summary",
        "start_timestamp",
        "end_timestamp",
        "generated_at",
        "disclaimer",
    }
    assert required <= payload.keys()
    assert payload["schema_version"] == "1.2.0"
    assert payload["strategy_name"] == "target_sequence"
    assert payload["strategy_configuration"]["targets"] == "0.1,0.0,0.0"
    assert payload["backtest_assumptions"]["execution_timing"] == "next_bar_open"
    assert payload["result_summary"]["realized_pnl"] > 0
    assert "not financial advice" in payload["disclaimer"]
    assert "synthetic test fixture" in payload["warnings"]


def test_report_rejects_assumptions_that_do_not_match_the_run(tmp_path: Path) -> None:
    result = completed_result()

    with pytest.raises(ValueError, match="BacktestConfig does not match"):
        export_json_report(result, config(), tmp_path / "wrong-config.json")
    with pytest.raises(ValueError, match="RiskPolicy does not match"):
        export_json_report(
            result,
            config(fee_bps=1, slippage_bps=2),
            tmp_path / "wrong-policy.json",
            policy=RiskPolicy(
                allowed_symbols=("QQQ",),
                max_daily_loss_pct=1.0,
                max_drawdown_pct=1.0,
            ),
        )


def test_report_embeds_effective_stricter_configuration(tmp_path: Path) -> None:
    loose_policy = RiskPolicy(
        allowed_symbols=("SPY",),
        max_asset_weight=0.2,
        max_total_gross_exposure=0.4,
        max_order_notional_weight=0.2,
        max_daily_loss_pct=1.0,
        max_drawdown_pct=1.0,
    )
    strict_config = config(
        max_position_pct=0.1,
        max_total_exposure_pct=0.3,
        max_order_notional_pct=0.1,
    )
    result = run_backtest(
        make_bars([100]),
        strategy=NoTradeStrategy(),
        policy=loose_policy,
        config=strict_config,
    )
    path = export_json_report(
        result,
        strict_config,
        tmp_path / "effective.json",
        policy=loose_policy,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["risk_configuration"]["max_asset_weight"] == 0.1
    assert payload["risk_configuration"]["max_total_gross_exposure"] == 0.3
    assert payload["risk_configuration"]["max_order_notional_weight"] == 0.1


def test_equity_csv_has_documented_accounting_columns(tmp_path: Path) -> None:
    rows = list(
        csv.DictReader(
            export_equity_csv(completed_result(), tmp_path / "equity.csv").open(
                newline="",
                encoding="utf-8",
            )
        )
    )

    assert rows
    assert list(rows[0]) == [
        "timestamp",
        "close",
        "cash",
        "quantity",
        "average_cost",
        "position_market_value",
        "equity",
        "start_of_day_equity",
        "daily_pnl",
        "peak_equity",
        "exposure",
        "realized_pnl",
        "unrealized_pnl",
        "cumulative_fees",
        "cumulative_slippage",
        "drawdown",
        "halt_state",
    ]
    assert rows[0]["timestamp"].endswith("+00:00")


def test_trade_csv_exports_only_approved_fills(tmp_path: Path) -> None:
    result = completed_result()
    rows = list(
        csv.DictReader(
            export_trades_csv(result, tmp_path / "trades.csv").open(
                newline="",
                encoding="utf-8",
            )
        )
    )

    assert len(rows) == len(result.fills) == 2
    assert {row["side"] for row in rows} == {"buy", "sell"}
    assert {row["execution_phase"] for row in rows} == {"open"}
    assert all(float(row["notional"]) > 0 for row in rows)


def test_rejected_intents_csv_includes_typed_reasons(tmp_path: Path) -> None:
    result = run_backtest(
        make_bars([100, 100]),
        strategy=TargetSequenceStrategy((0.2, 0.2)),
        policy=policy(),
        config=config(),
    )
    rows = list(
        csv.DictReader(
            export_rejected_intents_csv(result, tmp_path / "rejected.csv").open(
                newline="",
                encoding="utf-8",
            )
        )
    )

    assert len(rows) == 1
    assert "order notional exceeds max order weight" in rows[0]["reasons"]


def test_completed_paper_replay_matches_batch_backtest() -> None:
    bars = make_bars([100, 105, 110], opens=[100, 100, 110])
    batch = run_backtest(
        bars,
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=config(),
    )
    session = HistoricalReplaySession(
        bars,
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        backtest_config=config(),
    )

    summary = session.run_to_completion()

    assert summary.status is PaperSessionStatus.COMPLETED
    assert summary.result.summary == batch.summary
    assert summary.result.fills == batch.fills
    assert summary.session_id == batch.session_id


def test_strategy_parameters_are_part_of_the_session_identity() -> None:
    bars = make_bars([100, 101, 102, 103, 104, 105])
    first = run_backtest(
        bars,
        strategy=MovingAverageStrategy(fast_window=2, slow_window=4),
        policy=policy(),
        config=config(),
    )
    second = run_backtest(
        bars,
        strategy=MovingAverageStrategy(fast_window=3, slow_window=5),
        policy=policy(),
        config=config(),
    )

    assert first.session_id != second.session_id
    assert dict(first.strategy_configuration) == {
        "fast_window": 2,
        "slow_window": 4,
        "target_weight": 0.1,
    }


def test_paper_pause_resume_and_stop_are_explicit() -> None:
    session = HistoricalReplaySession(
        make_bars([100, 101, 102]),
        strategy=NoTradeStrategy(),
        policy=policy(),
        backtest_config=config(),
    )

    session.start()
    session.step()
    session.pause()
    session.resume()
    session.step()
    session.stop()
    summary = session.summary()

    assert summary.status is PaperSessionStatus.STOPPED
    assert summary.bars_processed == 2
    assert [transition.to_status for transition in summary.transitions] == [
        PaperSessionStatus.VALIDATED,
        PaperSessionStatus.RUNNING,
        PaperSessionStatus.PAUSED,
        PaperSessionStatus.RUNNING,
        PaperSessionStatus.STOPPED,
    ]


def test_manual_kill_switch_halts_paper_session_and_new_risk() -> None:
    session = HistoricalReplaySession(
        make_bars([100, 101, 102]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=policy(),
        backtest_config=config(),
    )
    session.start()
    session.step()
    session.activate_kill_switch()

    summary = session.summary()

    assert summary.status is PaperSessionStatus.HALTED
    assert summary.result.halt_state.active
    assert summary.result.summary.number_of_trades == 0


def test_paper_strategy_never_receives_future_rows() -> None:
    strategy = TargetSequenceStrategy((0.0, 0.0, 0.0, 0.0))
    session = HistoricalReplaySession(
        make_bars([100, 101, 102, 103]),
        strategy=strategy,
        policy=policy(),
        backtest_config=config(),
    )

    session.run_to_completion()

    assert strategy.history_lengths == [1, 2, 3, 4]


def test_paper_bar_failure_rolls_back_engine_state_and_is_terminal() -> None:
    class SecondBarMutationStrategy:
        name = "second_bar_mutation"
        calls = 0
        engine: object | None = None

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            self.calls += 1
            latest = history[-1]
            if self.calls == 2:
                assert self.engine is not None
                self.engine._cash = 1  # type: ignore[attr-defined]
            return Signal(latest.timestamp, latest.symbol, 0.1, self.name)

    strategy = SecondBarMutationStrategy()
    events: list[dict[str, object]] = []
    session = HistoricalReplaySession(
        make_bars([100, 100]),
        strategy=strategy,
        policy=policy(),
        backtest_config=config(),
        event_sink=events.append,
    )
    strategy.engine = session._engine
    session.start()
    session.step()
    committed_event_count = len(events)

    with pytest.raises(RuntimeError, match="mutate protected engine state"):
        session.step()

    summary = session.summary()
    assert session.status is PaperSessionStatus.FAILED
    assert session.bars_processed == summary.bars_processed == 1
    assert summary.result is not None
    assert summary.result.input_metadata.row_count == 2
    assert summary.result.fills == ()
    assert len(events) == committed_event_count + 1
    assert events[-1]["event"] == "session_failed"
    assert events[-1]["to_status"] == "failed"
    assert not any(event["event"] == "fill_created" for event in events)
    assert strategy.calls == 2
    assert summary.transitions[-1].to_status is PaperSessionStatus.FAILED
    assert (
        sum(transition.to_status is PaperSessionStatus.FAILED for transition in summary.transitions)
        == 1
    )
    assert summary.transitions[-1].timestamp == make_bars([100, 100])[1].timestamp
    with pytest.raises(RuntimeError, match="must be running"):
        session.step()
    assert strategy.calls == 2


def test_replay_scheduler_failure_is_terminal_and_preserves_committed_state(
    tmp_path: Path,
) -> None:
    events: list[dict[str, object]] = []

    def fail_sleep(_seconds: float) -> None:
        raise OSError("synthetic scheduler failure")

    session = HistoricalReplaySession(
        make_bars([100, 101, 102]),
        strategy=NoTradeStrategy(),
        policy=policy(),
        backtest_config=config(),
        replay_config=PaperReplayConfig(0.25),
        event_sink=events.append,
        sleeper=fail_sleep,
    )

    with pytest.raises(OSError, match="synthetic scheduler failure"):
        session.run_to_completion()

    summary = session.summary()
    assert summary.status is PaperSessionStatus.FAILED
    assert summary.bars_processed == 1
    assert summary.result is not None
    assert len(summary.result.equity_curve) == 1
    assert summary.failure_reason == "replay_runtime_failed:OSError"
    assert [event["event"] for event in events[-2:]] == [
        "pending_signal_expired",
        "session_failed",
    ]
    payload = json.loads(
        export_paper_session_json(summary, tmp_path / "failed.json").read_text(encoding="utf-8")
    )
    assert payload["final_state"] == "failed"
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "replay_runtime_failed:OSError"


def test_structured_event_sink_io_failure_becomes_one_simulation_warning(
    tmp_path: Path,
) -> None:
    class FailingStream:
        def seek(self, *_args: object) -> None:
            return None

        def tell(self) -> int:
            return 0

        def write(self, _message: str) -> None:
            raise OSError("synthetic disk failure")

        def flush(self) -> None:
            return None

    sink = StructuredEventSink(tmp_path / "events.jsonl")
    handler = sink._logger.handlers[0]
    original_stream = handler.stream
    handler.stream = FailingStream()
    try:
        result = run_backtest(
            make_bars([100, 101]),
            strategy=NoTradeStrategy(),
            policy=policy(),
            config=config(),
            event_sink=sink,
        )
    finally:
        handler.stream = original_stream
        sink.close()

    assert result.summary.warning_count == 1
    assert [warning.code for warning in result.warnings] == [WarningCode.EVENT_SINK_FAILURE]


def test_event_sink_failure_is_recorded_without_splitting_replay_state(
    tmp_path: Path,
) -> None:
    attempts = 0

    def failing_sink(_event: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts % 2:
            raise OSError("synthetic local sink failure")
        raise RuntimeError("different synthetic local sink failure")

    session = HistoricalReplaySession(
        make_bars([100]),
        strategy=NoTradeStrategy(),
        policy=policy(),
        backtest_config=config(),
        event_sink=failing_sink,
    )

    summary = session.run_to_completion()

    assert session.status is PaperSessionStatus.COMPLETED
    assert session.bars_processed == summary.bars_processed == 1
    assert summary.result.input_metadata.row_count == 1
    assert summary.result.summary.warning_count == 1
    assert summary.result.warnings[0].code is WarningCode.EVENT_SINK_FAILURE
    assert "OSError" in summary.result.warnings[0].message

    payload = json.loads(
        export_paper_session_json(summary, tmp_path / "paper.json").read_text(encoding="utf-8")
    )
    assert payload["data_validation_warnings"] == []
    assert payload["simulation_warnings"][0]["code"] == "event_sink_failure"


def test_structured_event_sink_close_returns_a_typed_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = StructuredEventSink(tmp_path / "events.jsonl")
    handler = sink._logger.handlers[0]

    def failing_flush() -> None:
        raise OSError("synthetic close failure")

    monkeypatch.setattr(handler, "flush", failing_flush)

    warning = sink.close()

    assert warning is not None
    assert warning.code is WarningCode.EVENT_SINK_FAILURE
    assert "OSError" in warning.message
    assert sink._logger.handlers == []


def test_paper_session_json_is_reproducible_and_local(tmp_path: Path) -> None:
    session = HistoricalReplaySession(
        make_bars([100, 101]),
        strategy=NoTradeStrategy(),
        policy=policy(),
        backtest_config=config(),
    )
    path = export_paper_session_json(session.run_to_completion(), tmp_path / "paper.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["mode"] == "historical_paper_replay"
    assert payload["schema_version"] == "1.2.0"
    assert payload["status"] == "completed"
    assert payload["bars_processed"] == 2
    assert payload["backtest_assumptions"]["execution_timing"] == "next_bar_open"
    assert payload["risk_configuration"]["allow_live_trading"] is False
    assert payload["input_data"]["row_count"] == 2
    assert "no external API" in payload["disclaimer"]
    assert "not financial advice" in payload["disclaimer"]


def test_structured_log_contains_required_session_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    with StructuredEventSink(log_path) as sink:
        result = run_backtest(
            make_bars([100, 105, 110], opens=[100, 100, 110]),
            strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
            policy=policy(),
            config=config(),
            event_sink=sink,
        )

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events
    assert {
        "bar_received",
        "signal_generated",
        "intent_created",
        "risk_accepted",
        "fill_created",
        "portfolio_updated",
    } <= {event["event"] for event in events}
    for event in events:
        assert event["session_id"] == result.session_id
        assert event["strategy_name"] == result.strategy_name
        assert event["symbol"] == "SPY"
        assert event["event_timestamp"].endswith("+00:00")


def test_package_cli_validates_runs_and_exports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    json_path = tmp_path / "summary.json"
    equity_path = tmp_path / "equity.csv"

    assert main(["validate-csv"]) == 0
    assert (
        main(
            [
                "backtest",
                "--fee-bps",
                "1",
                "--slippage-bps",
                "2",
                "--export-json",
                str(json_path),
                "--export-csv",
                str(equity_path),
            ]
        )
        == 0
    )
    assert main(["paper-replay"]) == 0
    assert main(["show-config"]) == 0

    output = capsys.readouterr().out
    assert "Resolved configuration" in output
    assert "Local historical paper replay" in output
    assert json_path.exists() and equity_path.exists()


def test_backtest_cli_attaches_structured_sink_close_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = StructuredEventSink.close

    def close_with_warning(self: StructuredEventSink) -> DataWarning:
        assert original_close(self) is None
        return DataWarning(
            WarningCode.EVENT_SINK_FAILURE,
            "synthetic close failure; events may be missing",
        )

    monkeypatch.setattr(StructuredEventSink, "close", close_with_warning)
    report_path = tmp_path / "summary.json"

    assert (
        main(
            [
                "backtest",
                "--log-jsonl",
                str(tmp_path / "events.jsonl"),
                "--export-json",
                str(report_path),
            ]
        )
        == 0
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["result_summary"]["warning_count"] == 1
    assert payload["simulation_warnings"][0]["code"] == "event_sink_failure"


def test_paper_cli_rejects_unreachable_lifecycle_controls(
    capsys: pytest.CaptureFixture,
) -> None:
    assert main(["paper-replay", "--kill-switch-after-bars", "15"]) == 2

    assert "must be between 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "artifact",
    [
        "reports/summary.json",
        "reports/equity.csv",
        "reports/trades.csv",
        "reports/rejected.csv",
        "reports/risk-events.csv",
        "logs/session.jsonl",
        "checkpoints/paper-session.json",
        "data/local/SPY.csv",
    ],
)
def test_generated_and_local_artifacts_are_ignored_by_git(artifact: str) -> None:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", artifact],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("git is not available")

    assert result.returncode == 0


def test_csv_to_backtest_and_reports_integration(tmp_path: Path) -> None:
    dataset = load_market_data_csv(Path("data/sample/synthetic_spy_daily.csv"))
    result = run_backtest(
        dataset.bars,
        strategy=NoTradeStrategy(),
        policy=RiskPolicy(allowed_symbols=("SPY",)),
        metadata=dataset.metadata,
        warnings=dataset.warnings,
    )

    assert result.input_metadata.source.endswith("synthetic_spy_daily.csv")
    assert export_json_report(result, BacktestConfig(), tmp_path / "result.json").exists()
    assert export_equity_csv(result, tmp_path / "equity.csv").exists()
