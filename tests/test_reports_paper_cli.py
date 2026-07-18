from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from tests.support import TargetSequenceStrategy, make_bars
from trading_bot_lab.backtesting import (
    BacktestConfig,
    NoTradeStrategy,
    export_equity_csv,
    export_json_report,
    export_rejected_intents_csv,
    export_trades_csv,
    load_market_data_csv,
    run_backtest,
)
from trading_bot_lab.cli import main
from trading_bot_lab.domain import PaperSessionStatus
from trading_bot_lab.observability import StructuredEventSink
from trading_bot_lab.paper import HistoricalReplaySession, export_paper_session_json
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
    assert payload["schema_version"] == "1.0.0"
    assert payload["strategy_name"] == "target_sequence"
    assert payload["result_summary"]["realized_pnl"] > 0
    assert "not financial advice" in payload["disclaimer"]
    assert "synthetic test fixture" in payload["warnings"]


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
    assert payload["status"] == "completed"
    assert payload["bars_processed"] == 2
    assert "no external API" in payload["disclaimer"]


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
    assert {"bar_processed", "risk_decision", "fill"} <= {event["event"] for event in events}
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


def test_paper_cli_rejects_unreachable_lifecycle_controls(
    capsys: pytest.CaptureFixture,
) -> None:
    assert main(["paper-replay", "--kill-switch-after-bars", "15"]) == 2

    assert "must be between 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "artifact",
    [
        "reports/summary.json",
        "reports/equity.csv",
        "reports/trades.csv",
        "reports/rejected.csv",
        "logs/session.jsonl",
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
