"""Command-line interface for local validation, backtests, and paper replay."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
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
    export_risk_events_csv,
    export_trades_csv,
    load_market_data_csv,
    run_backtest,
)
from trading_bot_lab.domain import (
    BacktestResult,
    DataValidationError,
    DataWarning,
    PaperSessionStatus,
    PaperSessionSummary,
)
from trading_bot_lab.observability import StructuredEventSink, structured_log_artifact_paths
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
    except (DataValidationError, ValueError, RuntimeError, OSError) as exc:
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
    paper.add_argument(
        "--replay-speed-seconds",
        "--speed",
        dest="replay_speed_seconds",
        type=float,
        default=0.0,
    )
    paper.add_argument("--random-seed", type=int, default=0)
    paper.add_argument("--pause-after-bars", type=int, default=None)
    paper.add_argument("--kill-switch-after-bars", type=int, default=None)
    paper.add_argument("--stop-after-bars", type=int, default=None)
    _add_export_arguments(paper, paper_mode=True)

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
    parser.add_argument("--strategy-history-bars", type=int, default=10_000)


def _add_strategy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast-window", type=int, default=3)
    parser.add_argument("--slow-window", type=int, default=5)


def _add_export_arguments(
    parser: argparse.ArgumentParser,
    *,
    paper_mode: bool = False,
) -> None:
    json_flags = ("--export-json", "--export-manifest") if paper_mode else ("--export-json",)
    parser.add_argument(*json_flags, dest="export_json", type=Path, default=None)
    parser.add_argument(
        "--export-csv",
        "--export-equity-csv",
        dest="export_equity_csv",
        type=Path,
        default=None,
    )
    parser.add_argument("--export-trades-csv", type=Path, default=None)
    parser.add_argument("--export-rejections-csv", type=Path, default=None)
    parser.add_argument("--export-risk-events-csv", type=Path, default=None)
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
    _validate_selected_artifact_paths(args, data_path=data_path)
    dataset = load_market_data_csv(data_path, config=_data_config(args))
    config = _backtest_config(args)
    policy = _risk_policy(dataset.metadata.symbol, config)
    strategy = MovingAverageStrategy(
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        target_weight=config.max_position_pct,
    )
    if config.strategy_history_limit < strategy.slow_window:
        raise ValueError("strategy_history_bars must be greater than or equal to slow_window")
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
        close_warning = sink.close() if sink is not None else None
    if close_warning is not None:
        result = _append_result_warning(result, close_warning)

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
    if args.export_risk_events_csv:
        print(
            "Wrote risk events CSV:",
            export_risk_events_csv(result, args.export_risk_events_csv),
        )
    if any(
        value is not None
        for value in (
            args.export_json,
            args.export_equity_csv,
            args.export_trades_csv,
            args.export_rejections_csv,
            args.export_risk_events_csv,
            args.log_jsonl,
        )
    ):
        print("Artifact note: generated reports and logs are local and ignored by Git.")
    return 0


def _paper_replay(args: argparse.Namespace) -> int:
    data_path, default_path = _data_path(args.csv_path)
    _validate_selected_artifact_paths(args, data_path=data_path)
    dataset = load_market_data_csv(data_path, config=_data_config(args))
    if args.pause_after_bars is not None and not 0 < args.pause_after_bars < len(dataset.bars):
        raise ValueError("pause_after_bars must be between 1 and one less than the data row count")
    for name, value in (
        ("kill_switch_after_bars", args.kill_switch_after_bars),
        ("stop_after_bars", args.stop_after_bars),
    ):
        if value is not None and not 0 <= value < len(dataset.bars):
            raise ValueError(f"{name} must be between 0 and one less than the data row count")
    if args.kill_switch_after_bars is not None and args.stop_after_bars is not None:
        raise ValueError("kill_switch_after_bars and stop_after_bars are mutually exclusive")
    terminal_after = (
        args.kill_switch_after_bars
        if args.kill_switch_after_bars is not None
        else args.stop_after_bars
    )
    if (
        args.pause_after_bars is not None
        and terminal_after is not None
        and args.pause_after_bars > terminal_after
    ):
        raise ValueError("pause_after_bars cannot occur after a terminal replay control")
    result_exports_requested = any(
        value is not None
        for value in (
            args.export_equity_csv,
            args.export_trades_csv,
            args.export_rejections_csv,
            args.export_risk_events_csv,
        )
    )
    if terminal_after == 0 and result_exports_requested:
        raise ValueError("result CSV exports require at least one processed replay bar")
    config = _backtest_config(args)
    policy = _risk_policy(dataset.metadata.symbol, config)
    replay = PaperReplayConfig(args.replay_speed_seconds, args.random_seed)
    strategy = MovingAverageStrategy(
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        target_weight=config.max_position_pct,
    )
    if config.strategy_history_limit < strategy.slow_window:
        raise ValueError("strategy_history_bars must be greater than or equal to slow_window")
    _print_resolved_config("historical_paper_replay", config, policy, strategy, replay)
    sink = StructuredEventSink(args.log_jsonl) if args.log_jsonl else None
    failure: Exception | None = None
    summary: PaperSessionSummary
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
        try:
            if args.kill_switch_after_bars == 0:
                session.activate_kill_switch()
            elif args.stop_after_bars == 0:
                session.stop()
            else:
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
                    if (
                        args.stop_after_bars is not None
                        and session.status is PaperSessionStatus.RUNNING
                        and session.bars_processed == args.stop_after_bars
                    ):
                        session.stop()
                    if (
                        session.status is PaperSessionStatus.RUNNING
                        and replay.replay_speed_seconds > 0
                    ):
                        time.sleep(replay.replay_speed_seconds)
        except Exception as exc:
            failure = exc
        summary = session.summary()
    finally:
        close_warning = sink.close() if sink is not None else None
    if close_warning is not None:
        summary = _append_summary_warning(summary, close_warning)

    print("Local historical paper replay")
    print("Mode: simulated only; no network, broker, exchange, or real orders.")
    print(f"Data: {data_path}")
    print(f"Status: {summary.status.value}")
    print(f"Bars processed: {summary.bars_processed}/{summary.total_bars}")
    print(f"Session ID: {summary.session_id}")
    if summary.result is not None:
        print(f"Ending equity: {summary.result.summary.ending_equity:.2f}")
        print(f"Risk halt triggered: {summary.result.summary.risk_halt_triggered}")
    else:
        print("Ending equity: not available (no bars processed)")
        print(f"Risk halt triggered: {bool(summary.halt_reasons)}")
    _print_warnings(summary.warnings)
    if data_path.resolve() == default_path.resolve():
        print("Data note: synthetic demo data only; no performance claim.")
    artifact_paths = _artifact_path_mapping(
        args,
        include_result_exports=summary.result is not None,
    )
    if summary.result is not None:
        if args.export_equity_csv:
            print(
                "Wrote paper equity CSV:",
                export_equity_csv(summary.result, args.export_equity_csv),
            )
        if args.export_trades_csv:
            print(
                "Wrote paper trades CSV:",
                export_trades_csv(summary.result, args.export_trades_csv),
            )
        if args.export_rejections_csv:
            print(
                "Wrote paper rejected intents CSV:",
                export_rejected_intents_csv(summary.result, args.export_rejections_csv),
            )
        if args.export_risk_events_csv:
            print(
                "Wrote paper risk events CSV:",
                export_risk_events_csv(summary.result, args.export_risk_events_csv),
            )
    if args.export_json:
        print(
            "Wrote paper session manifest:",
            export_paper_session_json(
                summary,
                args.export_json,
                artifact_paths=artifact_paths,
            ),
        )
    if artifact_paths:
        print("Artifact note: generated reports and logs are local and ignored by Git.")
    if failure is not None:
        print(f"Error: historical replay failed with {type(failure).__name__}", file=sys.stderr)
        return 2
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


def _append_result_warning(result: BacktestResult, warning: DataWarning) -> BacktestResult:
    if any(existing.code is warning.code for existing in result.warnings):
        return result
    selected = (*result.warnings, warning)
    return replace(
        result,
        warnings=selected,
        summary=replace(result.summary, warning_count=len(selected)),
    )


def _append_summary_warning(
    summary: PaperSessionSummary,
    warning: DataWarning,
) -> PaperSessionSummary:
    if any(existing.code is warning.code for existing in summary.warnings):
        return summary
    selected = (*summary.warnings, warning)
    result = summary.result
    if result is not None:
        result = _append_result_warning(result, warning)
    return replace(summary, warnings=selected, result=result)


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
        strategy_history_limit=args.strategy_history_bars,
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
    data_path = selected or default
    _validate_data_path(data_path, root)
    return data_path, default


def _validate_data_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if not _is_relative_to(resolved, root):
        return
    allowed_roots = tuple(
        (root / "data" / directory).resolve()
        for directory in ("sample", "local", "raw", "processed")
    )
    if not any(_is_relative_to(resolved, allowed) for allowed in allowed_roots):
        raise ValueError(
            "CSV paths inside the repository must be under data/sample, data/local, "
            "data/raw, or data/processed"
        )


def _validate_selected_artifact_paths(
    args: argparse.Namespace,
    *,
    data_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    report_root = (root / "reports").resolve()
    log_root = (root / "logs").resolve()
    selected_paths: list[tuple[str, Path]] = []
    for name in (
        "export_json",
        "export_equity_csv",
        "export_trades_csv",
        "export_rejections_csv",
        "export_risk_events_csv",
    ):
        selected = getattr(args, name, None)
        if selected is not None:
            _validate_artifact_path(selected, root=root, allowed_root=report_root)
            selected_paths.append((name, selected.resolve()))
    if getattr(args, "log_jsonl", None) is not None:
        for index, log_path in enumerate(structured_log_artifact_paths(args.log_jsonl)):
            _validate_artifact_path(log_path, root=root, allowed_root=log_root)
            name = "log_jsonl" if index == 0 else f"log_jsonl_backup_{index}"
            selected_paths.append((name, log_path.resolve()))

    input_key = str(data_path.resolve()).casefold()
    seen: dict[str, str] = {}
    seen_paths: list[tuple[str, Path]] = []
    for name, selected in selected_paths:
        key = str(selected).casefold()
        if key == input_key or _same_existing_file(selected, data_path):
            raise ValueError(f"{name} must not overwrite or append to the input CSV")
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(f"{name} and {previous} must use different artifact paths")
        for previous_name, previous_path in seen_paths:
            if _same_existing_file(selected, previous_path):
                raise ValueError(f"{name} and {previous_name} must use different artifact files")
        seen[key] = name
        seen_paths.append((name, selected))


def _validate_artifact_path(path: Path, *, root: Path, allowed_root: Path) -> None:
    resolved = path.resolve()
    if (
        _is_relative_to(resolved, root)
        and not _is_relative_to(resolved, allowed_root)
        and not _is_workspace_pytest_temp(resolved, root)
    ):
        raise ValueError(
            f"generated artifacts inside the repository must be under {allowed_root.name}/"
        )


def _artifact_path_mapping(
    args: argparse.Namespace,
    *,
    include_result_exports: bool,
) -> dict[str, Path]:
    selected: dict[str, Path] = {}
    for label, name in (
        ("manifest", "export_json"),
        ("equity_csv", "export_equity_csv"),
        ("trades_csv", "export_trades_csv"),
        ("rejections_csv", "export_rejections_csv"),
        ("risk_events_csv", "export_risk_events_csv"),
        ("event_log", "log_jsonl"),
    ):
        value = getattr(args, name, None)
        is_result_export = label in {
            "equity_csv",
            "trades_csv",
            "rejections_csv",
            "risk_events_csv",
        }
        if value is not None and (include_result_exports or not is_result_export):
            selected[label] = value
    return selected


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _same_existing_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return False


def _is_workspace_pytest_temp(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if not relative.parts:
        return False
    first = relative.parts[0]
    return first == ".pytest" or first.startswith((".pytest-", ".pytest_"))


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
    _print_warnings(result.warnings)
    print(
        f"Buy-and-hold benchmark: {result.benchmarks.buy_and_hold.ending_equity:.2f} ending equity"
    )
    print(f"Cash benchmark: {result.benchmarks.cash.ending_equity:.2f} ending equity")
    if data_path.resolve() == default_path.resolve():
        print("Data note: synthetic demo data only; results are not meaningful market evidence.")


def _print_warnings(warnings: tuple[DataWarning, ...]) -> None:
    print(f"Warnings: {len(warnings)}")
    for warning in warnings:
        print(f"Warning [{warning.code.value}]: {warning.message}")


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = ["build_parser", "main"]
