"""Command-line interface for local validation, backtests, and paper replay."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from trading_bot_lab.backtesting import (
    BacktestConfig,
    CsvDataConfig,
    GapPolicy,
    MissingVolumePolicy,
    MovingAverageStrategy,
    export_equity_csv,
    export_json_report,
    export_rejected_intents_csv,
    export_trades_csv,
    load_market_data_csv,
    run_backtest,
)
from trading_bot_lab.domain import DataValidationError, PaperSessionStatus
from trading_bot_lab.observability import StructuredEventSink
from trading_bot_lab.paper import (
    HistoricalReplaySession,
    PaperReplayConfig,
    export_paper_session_json,
)
from trading_bot_lab.risk import RiskPolicy


def main(argv: list[str] | None = None) -> int:
    """Run the package CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-csv":
            return _validate_csv(args)
        if args.command == "backtest":
            return _backtest(args)
        if args.command == "paper-replay":
            return _paper_replay(args)
        if args.command == "show-config":
            return _show_config()
    except (DataValidationError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    parser.error("a command is required")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m trading_bot_lab",
        description="Safe local trading research; no live trading path is implemented.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-csv", help="Validate one local CSV")
    _add_data_arguments(validate)

    backtest = subparsers.add_parser("backtest", help="Run a deterministic local backtest")
    _add_data_arguments(backtest)
    _add_simulation_arguments(backtest)
    _add_strategy_arguments(backtest)
    _add_export_arguments(backtest)

    paper = subparsers.add_parser(
        "paper-replay",
        help="Replay historical CSV rows through the local simulated paper layer",
    )
    _add_data_arguments(paper)
    _add_simulation_arguments(paper)
    _add_strategy_arguments(paper)
    paper.add_argument("--replay-speed-seconds", type=float, default=0.0)
    paper.add_argument("--pause-after-bars", type=int, default=None)
    paper.add_argument("--kill-switch-after-bars", type=int, default=None)
    paper.add_argument("--export-json", type=Path, default=None)
    paper.add_argument("--log-jsonl", type=Path, default=None)

    subparsers.add_parser("show-config", help="Print resolved safe default configuration")
    return parser


def _add_data_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=None,
        help="Local CSV path; defaults to committed synthetic demo data.",
    )
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--timeframe-seconds", type=int, default=86_400)
    parser.add_argument("--max-gap-days", type=int, default=7)
    parser.add_argument(
        "--missing-volume",
        choices=[policy.value for policy in MissingVolumePolicy],
        default=MissingVolumePolicy.WARN.value,
    )


def _add_simulation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--minimum-fee", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--max-position-pct", type=float, default=10.0)
    parser.add_argument("--max-total-exposure-pct", type=float, default=30.0)
    parser.add_argument("--max-order-notional-pct", type=float, default=10.0)
    parser.add_argument("--daily-loss-limit-pct", type=float, default=2.0)
    parser.add_argument("--max-drawdown-limit-pct", type=float, default=5.0)
    parser.add_argument("--warmup-bars", type=int, default=0)
    parser.add_argument("--data-age-seconds", type=int, default=0)


def _add_strategy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast-window", type=int, default=3)
    parser.add_argument("--slow-window", type=int, default=5)


def _add_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--export-json", type=Path, default=None)
    parser.add_argument(
        "--export-csv",
        "--export-equity-csv",
        dest="export_equity_csv",
        type=Path,
        default=None,
    )
    parser.add_argument("--export-trades-csv", type=Path, default=None)
    parser.add_argument("--export-rejections-csv", type=Path, default=None)
    parser.add_argument("--log-jsonl", type=Path, default=None)


def _validate_csv(args: argparse.Namespace) -> int:
    data_path, default_path = _data_path(args.csv_path)
    dataset = load_market_data_csv(data_path, config=_data_config(args))
    print("CSV validation passed")
    print(json.dumps(asdict(dataset.metadata), indent=2, default=_json_default))
    for warning in dataset.warnings:
        print(f"Warning [{warning.code.value}]: {warning.message}")
    if data_path.resolve() == default_path.resolve():
        print("Data note: committed synthetic demo data only; no performance claim.")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    data_path, default_path = _data_path(args.csv_path)
    dataset = load_market_data_csv(data_path, config=_data_config(args))
    config = _backtest_config(args)
    policy = _risk_policy(dataset.metadata.symbol, config)
    strategy = MovingAverageStrategy(
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        target_weight=config.max_position_pct,
    )
    _print_resolved_config("backtest", config, policy, strategy)
    sink = StructuredEventSink(args.log_jsonl) if args.log_jsonl else None
    try:
        result = run_backtest(
            dataset.bars,
            strategy=strategy,
            policy=policy,
            config=config,
            metadata=dataset.metadata,
            warnings=dataset.warnings,
            event_sink=sink,
        )
    finally:
        if sink is not None:
            sink.close()

    _print_backtest_summary(result, data_path, default_path)
    extra_warnings = (
        "Backtests are hypothetical research and are not financial advice.",
        "Synthetic sample results are not meaningful market results."
        if data_path.resolve() == default_path.resolve()
        else "User-provided market data must remain local and appropriately licensed.",
    )
    if args.export_json:
        print(
            "Wrote JSON report:",
            export_json_report(
                result,
                config,
                args.export_json,
                policy=policy,
                warnings=extra_warnings,
            ),
        )
    if args.export_equity_csv:
        print("Wrote equity CSV:", export_equity_csv(result, args.export_equity_csv))
    if args.export_trades_csv:
        print("Wrote trades CSV:", export_trades_csv(result, args.export_trades_csv))
    if args.export_rejections_csv:
        print(
            "Wrote rejected intents CSV:",
            export_rejected_intents_csv(result, args.export_rejections_csv),
        )
    if any(
        value is not None
        for value in (
            args.export_json,
            args.export_equity_csv,
            args.export_trades_csv,
            args.export_rejections_csv,
            args.log_jsonl,
        )
    ):
        print("Artifact note: generated reports and logs are local and ignored by Git.")
    return 0


def _paper_replay(args: argparse.Namespace) -> int:
    data_path, default_path = _data_path(args.csv_path)
    dataset = load_market_data_csv(data_path, config=_data_config(args))
    for name, value in (
        ("pause_after_bars", args.pause_after_bars),
        ("kill_switch_after_bars", args.kill_switch_after_bars),
    ):
        if value is not None and not 0 < value < len(dataset.bars):
            raise ValueError(f"{name} must be between 1 and one less than the data row count")
    config = _backtest_config(args)
    policy = _risk_policy(dataset.metadata.symbol, config)
    replay = PaperReplayConfig(args.replay_speed_seconds)
    strategy = MovingAverageStrategy(
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        target_weight=config.max_position_pct,
    )
    _print_resolved_config("historical_paper_replay", config, policy, strategy, replay)
    sink = StructuredEventSink(args.log_jsonl) if args.log_jsonl else None
    try:
        session = HistoricalReplaySession(
            dataset.bars,
            strategy=strategy,
            policy=policy,
            backtest_config=config,
            replay_config=replay,
            metadata=dataset.metadata,
            warnings=dataset.warnings,
            event_sink=sink,
        )
        session.start()
        while session.status is PaperSessionStatus.RUNNING:
            session.step()
            if (
                args.pause_after_bars is not None
                and session.status is PaperSessionStatus.RUNNING
                and session.bars_processed == args.pause_after_bars
            ):
                session.pause()
                session.resume()
            if (
                args.kill_switch_after_bars is not None
                and session.status is PaperSessionStatus.RUNNING
                and session.bars_processed == args.kill_switch_after_bars
            ):
                session.activate_kill_switch()
            if session.status is PaperSessionStatus.RUNNING and replay.replay_speed_seconds > 0:
                time.sleep(replay.replay_speed_seconds)
        summary = session.summary()
    finally:
        if sink is not None:
            sink.close()

    print("Local historical paper replay")
    print("Mode: simulated only; no network, broker, exchange, or real orders.")
    print(f"Data: {data_path}")
    print(f"Status: {summary.status.value}")
    print(f"Bars processed: {summary.bars_processed}/{summary.total_bars}")
    print(f"Session ID: {summary.session_id}")
    print(f"Ending equity: {summary.result.summary.ending_equity:.2f}")
    print(f"Risk halt triggered: {summary.result.summary.risk_halt_triggered}")
    if data_path.resolve() == default_path.resolve():
        print("Data note: synthetic demo data only; no performance claim.")
    if args.export_json:
        print("Wrote paper session JSON:", export_paper_session_json(summary, args.export_json))
    return 0


def _show_config() -> int:
    config = BacktestConfig()
    policy = RiskPolicy()
    replay = PaperReplayConfig()
    payload = {
        "mode": "backtest",
        "backtest": asdict(config),
        "risk": asdict(policy),
        "paper_replay": asdict(replay),
        "live_mode": "not implemented",
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))
    return 0


def _data_config(args: argparse.Namespace) -> CsvDataConfig:
    if args.max_gap_days <= 0:
        raise ValueError("max_gap_days must be positive")
    return CsvDataConfig(
        expected_symbol=args.symbol,
        timeframe_seconds=args.timeframe_seconds,
        missing_volume_policy=MissingVolumePolicy(args.missing_volume),
        max_gap_seconds=args.max_gap_days * 86_400,
        gap_policy=GapPolicy.WARN,
    )


def _backtest_config(args: argparse.Namespace) -> BacktestConfig:
    return BacktestConfig(
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        minimum_fee=args.minimum_fee,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct / 100,
        max_total_exposure_pct=args.max_total_exposure_pct / 100,
        max_order_notional_pct=args.max_order_notional_pct / 100,
        max_daily_loss_pct=args.daily_loss_limit_pct / 100,
        max_drawdown_pct=args.max_drawdown_limit_pct / 100,
        warmup_bars=args.warmup_bars,
        data_age_seconds=args.data_age_seconds,
    )


def _risk_policy(symbol: str, config: BacktestConfig) -> RiskPolicy:
    return RiskPolicy(
        max_asset_weight=config.max_position_pct,
        max_total_gross_exposure=config.max_total_exposure_pct,
        max_order_notional_weight=config.max_order_notional_pct,
        max_daily_loss_pct=config.max_daily_loss_pct,
        max_drawdown_pct=config.max_drawdown_pct,
        max_open_positions=config.max_open_positions,
        allowed_symbols=(symbol,),
    )


def _data_path(selected: Path | None) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    default = root / "data" / "sample" / "synthetic_spy_daily.csv"
    return (selected or default), default


def _print_resolved_config(
    mode: str,
    config: BacktestConfig,
    policy: RiskPolicy,
    strategy: MovingAverageStrategy,
    replay: PaperReplayConfig | None = None,
) -> None:
    payload: dict[str, object] = {
        "mode": mode,
        "backtest": asdict(config),
        "risk": asdict(policy),
        "strategy": asdict(strategy),
    }
    if replay is not None:
        payload["paper_replay"] = asdict(replay)
    print("Resolved configuration:")
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))


def _print_backtest_summary(result, data_path: Path, default_path: Path) -> None:
    summary = result.summary
    print("Local deterministic CSV backtest")
    print(f"Data: {data_path}")
    print("Mode: backtest only; no broker, exchange, network call, or API key used.")
    print(f"Symbol: {result.symbol}")
    print(f"Start: {summary.start_timestamp.isoformat()}")
    print(f"End: {summary.end_timestamp.isoformat()}")
    print(f"Starting cash: {summary.starting_cash:.2f}")
    print(f"Ending equity: {summary.ending_equity:.2f}")
    print(f"Total return: {summary.total_return:.4%}")
    print(f"Max drawdown: {summary.max_drawdown:.4%}")
    print(f"Trades: {summary.number_of_trades}")
    print(f"Turnover: {summary.turnover:.4%}")
    print(f"Fees: {summary.total_fees_paid:.2f}")
    print(f"Estimated slippage: {summary.estimated_slippage_cost:.2f}")
    print(f"Realized PnL: {summary.realized_pnl:.2f}")
    print(f"Unrealized PnL: {summary.unrealized_pnl:.2f}")
    print(f"Average exposure: {summary.average_exposure:.4%}")
    print(f"Maximum exposure: {summary.max_exposure:.4%}")
    print(f"Risk halt triggered: {summary.risk_halt_triggered}")
    print(f"Rejected intents: {summary.rejected_order_count}")
    print(f"Warnings: {summary.warning_count}")
    print(
        f"Buy-and-hold benchmark: {result.benchmarks.buy_and_hold.ending_equity:.2f} ending equity"
    )
    print(f"Cash benchmark: {result.benchmarks.cash.ending_equity:.2f} ending equity")
    if data_path.resolve() == default_path.resolve():
        print("Data note: synthetic demo data only; results are not meaningful market evidence.")


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = ["build_parser", "main"]
