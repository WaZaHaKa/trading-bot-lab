"""Export deterministic observations from the independent local Python oracle."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.backtesting.csv_data import (
    CsvDataConfig,
    GapPolicy,
    MissingVolumePolicy,
    load_market_data_csv,
)
from trading_bot_lab.backtesting.engine import ENGINE_VERSION, run_backtest
from trading_bot_lab.backtesting.moving_average import MovingAverageStrategy
from trading_bot_lab.domain import BacktestConfig, BacktestResult, RiskStatus
from trading_bot_lab.parity.contract import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    DEFAULT_SCENARIO_PATH,
    TRACE_SCHEMA_VERSION,
    ParityContractError,
    decimal_string,
    deterministic_json,
    load_contract_bundle,
    load_scenario_bundle,
    parse_decimal_string,
)
from trading_bot_lab.risk import RiskPolicy


def build_local_parity_trace(
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> dict[str, Any]:
    """Run a scenario through the local engine and return a normalized v1 trace."""

    contract = load_contract_bundle()
    scenario = load_scenario_bundle(scenario_path)
    manifest = scenario.manifest
    assumptions = _object(manifest, "assumptions")
    strategy_config = _object(manifest, "strategy")
    risk_config = _object(manifest, "risk")

    symbol = _string(manifest, "symbol")
    timeframe_seconds = _integer(manifest, "timeframe_seconds")
    dataset = load_market_data_csv(
        scenario.fixture_path,
        config=CsvDataConfig(
            expected_symbol=symbol,
            timeframe_seconds=timeframe_seconds,
            missing_volume_policy=MissingVolumePolicy.REJECT,
            max_gap_seconds=4 * 86_400,
            gap_policy=GapPolicy.REJECT,
        ),
    )
    if dataset.metadata.content_sha256 != scenario.fixture_sha256:
        raise ParityContractError("CSV loader content hash differs from scenario fixture hash")

    config = _backtest_config(assumptions)
    policy = _risk_policy(risk_config)
    strategy = MovingAverageStrategy(
        fast_window=_integer(strategy_config, "fast_window"),
        slow_window=_integer(strategy_config, "slow_window"),
        target_weight=_decimal_float(strategy_config, "target_weight"),
    )
    if _string(strategy_config, "name") != strategy.name:
        raise ParityContractError("scenario strategy name does not match local strategy")

    result = run_backtest(
        dataset.bars,
        strategy=strategy,
        policy=policy,
        config=config,
        metadata=dataset.metadata,
        warnings=dataset.warnings,
    )
    trace = _normalize_result(
        result,
        bars=dataset.bars,
        scenario_manifest=manifest,
        fixture_sha256=scenario.fixture_sha256,
        scenario_manifest_sha256=scenario.manifest_sha256,
        contract_sha256=contract.contract_sha256,
        scenario_schema_sha256=contract.scenario_schema_sha256,
        trace_schema_sha256=contract.trace_schema_sha256,
    )
    _validate_expected_outcome(trace, _object(manifest, "expected"))
    return trace


def write_local_parity_trace(
    output_path: str | Path,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> Path:
    """Atomically write one deterministic local-oracle trace."""

    trace = build_local_parity_trace(scenario_path)
    return atomic_write_text(output_path, deterministic_json(trace))


def _normalize_result(
    result: BacktestResult,
    *,
    bars: tuple[Any, ...],
    scenario_manifest: dict[str, Any],
    fixture_sha256: str,
    scenario_manifest_sha256: str,
    contract_sha256: str,
    scenario_schema_sha256: str,
    trace_schema_sha256: str,
) -> dict[str, Any]:
    intent_indexes = {intent.intent_id: index for index, intent in enumerate(result.order_intents)}
    fill_indexes = {fill.intent_id: index for index, fill in enumerate(result.fills)}
    normalized_bars = []
    for index, (bar, point) in enumerate(zip(bars, result.equity_curve, strict=True)):
        normalized_bars.append(
            {
                "average_cost": decimal_string(point.average_cost),
                "cash": decimal_string(point.cash),
                "close": decimal_string(bar.close),
                "cumulative_fees": decimal_string(point.cumulative_fees),
                "cumulative_slippage": decimal_string(point.cumulative_slippage),
                "daily_pnl": decimal_string(point.daily_pnl),
                "drawdown": decimal_string(point.drawdown),
                "equity": decimal_string(point.equity),
                "exposure_pct": decimal_string(point.exposure_pct),
                "halted": point.halt_state.active,
                "high": decimal_string(_required_number(bar.high, "bar.high")),
                "index": index,
                "low": decimal_string(_required_number(bar.low, "bar.low")),
                "open": decimal_string(_required_number(bar.open, "bar.open")),
                "peak_equity": decimal_string(point.peak_equity),
                "position_market_value": decimal_string(point.position_market_value),
                "quantity": decimal_string(point.quantity),
                "realized_pnl": decimal_string(point.realized_pnl),
                "start_of_day_equity": decimal_string(point.start_of_day_equity),
                "symbol": bar.symbol,
                "target_weight_for_next_bar": decimal_string(point.target_weight_for_next_bar),
                "timestamp": bar.timestamp.isoformat(),
                "unrealized_pnl": decimal_string(point.unrealized_pnl),
                "volume": decimal_string(_required_number(bar.volume, "bar.volume")),
            }
        )

    normalized_intents = [
        {
            "estimated_execution_price": decimal_string(intent.estimated_execution_price),
            "estimated_fee": decimal_string(intent.estimated_fee),
            "execution_phase": intent.execution_phase.value,
            "execution_timestamp": intent.execution_timestamp.isoformat(),
            "index": index,
            "notional": decimal_string(intent.notional),
            "quantity": decimal_string(intent.quantity),
            "reference_price": decimal_string(intent.reference_price),
            "side": intent.side.value,
            "signal_timestamp": intent.signal_timestamp.isoformat(),
            "symbol": intent.symbol,
            "target_weight": decimal_string(intent.target_weight),
        }
        for index, intent in enumerate(result.order_intents)
    ]
    normalized_risk = [
        {
            "index": index,
            "intent_index": (
                None if decision.intent_id is None else intent_indexes[decision.intent_id]
            ),
            "metrics": {
                key: decimal_string(value) for key, value in sorted(decision.metrics.items())
            },
            "reasons": [reason.value for reason in decision.reasons],
            "status": decision.status.value,
            "timestamp": decision.timestamp.isoformat(),
        }
        for index, decision in enumerate(result.risk_decisions)
    ]
    normalized_fills = [
        {
            "execution_phase": fill.execution_phase.value,
            "execution_price": decimal_string(fill.execution_price),
            "fee": decimal_string(fill.fee),
            "index": index,
            "intent_index": intent_indexes[fill.intent_id],
            "quantity": decimal_string(fill.quantity),
            "reference_price": decimal_string(fill.reference_price),
            "side": fill.side.value,
            "slippage_cost": decimal_string(fill.slippage_cost),
            "symbol": fill.symbol,
            "timestamp": fill.timestamp.isoformat(),
        }
        for index, fill in enumerate(result.fills)
    ]
    normalized_trades = [
        {
            "average_cost_after": decimal_string(trade.average_cost_after),
            "fill_index": fill_indexes[trade.fill.intent_id],
            "fill_timestamp": trade.fill.timestamp.isoformat(),
            "index": index,
            "quantity": decimal_string(trade.fill.quantity),
            "realized_pnl_delta": decimal_string(trade.realized_pnl_delta),
            "resulting_cash": decimal_string(trade.resulting_cash),
            "resulting_quantity": decimal_string(trade.resulting_quantity),
            "side": trade.fill.side.value,
            "signal_timestamp": trade.signal_timestamp.isoformat(),
            "symbol": trade.fill.symbol,
            "target_weight": decimal_string(trade.target_weight),
        }
        for index, trade in enumerate(result.trades)
    ]

    summary = result.summary
    last_timestamp = result.equity_curve[-1].timestamp
    final_intents = [
        intent for intent in result.order_intents if intent.signal_timestamp == last_timestamp
    ]
    final_trades = [trade for trade in result.trades if trade.signal_timestamp == last_timestamp]
    rejected_count = sum(
        decision.status is RiskStatus.REJECTED and decision.intent_id is not None
        for decision in result.risk_decisions
    )
    return {
        "assumptions": _normalized_assumptions(result),
        "bars": normalized_bars,
        "contract": {
            "contract_name": CONTRACT_NAME,
            "contract_sha256": contract_sha256,
            "contract_version": CONTRACT_VERSION,
            "scenario_manifest_sha256": scenario_manifest_sha256,
            "scenario_schema_sha256": scenario_schema_sha256,
            "trace_schema_sha256": trace_schema_sha256,
        },
        "engine": {"name": "local_python_oracle", "version": ENGINE_VERSION},
        "fills": normalized_fills,
        "final_bar": {
            "creates_fill": bool(final_trades),
            "creates_intent": bool(final_intents),
            "pending_signal_unfilled": not final_intents and not final_trades,
            "target_weight": decimal_string(result.equity_curve[-1].target_weight_for_next_bar),
            "timestamp": last_timestamp.isoformat(),
        },
        "order_intents": normalized_intents,
        "provenance": "local_python_oracle_observation",
        "risk_decisions": normalized_risk,
        "scenario": {
            "bar_count": len(result.equity_curve),
            "end_timestamp": result.summary.end_timestamp.isoformat(),
            "fixture": scenario_manifest["fixture"],
            "fixture_sha256": fixture_sha256,
            "normalized_bars_sha256": result.input_metadata.bars_sha256,
            "scenario_id": scenario_manifest["scenario_id"],
            "start_timestamp": result.summary.start_timestamp.isoformat(),
            "symbol": result.symbol,
            "timeframe_seconds": result.input_metadata.timeframe_seconds,
        },
        "schema_version": TRACE_SCHEMA_VERSION,
        "strategy": {
            "configuration": {
                key: decimal_string(value) if isinstance(value, float) else value
                for key, value in result.strategy_configuration
            },
            "name": result.strategy_name,
        },
        "summary": {
            "average_exposure": decimal_string(summary.average_exposure),
            "ending_equity": decimal_string(summary.ending_equity),
            "estimated_slippage_cost": decimal_string(summary.estimated_slippage_cost),
            "halt_reasons": [reason.value for reason in result.halt_state.reasons],
            "max_drawdown": decimal_string(summary.max_drawdown),
            "max_exposure": decimal_string(summary.max_exposure),
            "number_of_fills": summary.number_of_trades,
            "realized_pnl": decimal_string(summary.realized_pnl),
            "rejected_order_count": rejected_count,
            "risk_halt_triggered": summary.risk_halt_triggered,
            "starting_cash": decimal_string(summary.starting_cash),
            "total_fees_paid": decimal_string(summary.total_fees_paid),
            "total_return": decimal_string(summary.total_return),
            "turnover": decimal_string(summary.turnover),
            "unrealized_pnl": decimal_string(summary.unrealized_pnl),
        },
        "trades": normalized_trades,
    }


def _normalized_assumptions(result: BacktestResult) -> dict[str, Any]:
    config = result.assumptions
    risk = result.risk_configuration
    return {
        "backtest": {
            "data_age_seconds": config.data_age_seconds,
            "execution_timing": config.execution_timing.value,
            "fee_bps": decimal_string(config.fee_bps),
            "fee_model": "notional_bps",
            "initial_cash": decimal_string(config.initial_cash),
            "kill_switch_active": config.kill_switch_active,
            "max_daily_loss_pct": decimal_string(config.max_daily_loss_pct),
            "max_drawdown_pct": decimal_string(config.max_drawdown_pct),
            "max_open_positions": config.max_open_positions,
            "max_order_notional_pct": decimal_string(config.max_order_notional_pct),
            "max_position_pct": decimal_string(config.max_position_pct),
            "max_total_exposure_pct": decimal_string(config.max_total_exposure_pct),
            "minimum_fee": decimal_string(config.minimum_fee),
            "money_precision": config.money_precision,
            "quantity_precision": config.quantity_precision,
            "slippage_bps": decimal_string(config.slippage_bps),
            "slippage_model": "adverse_bps",
            "strategy_history_limit": config.strategy_history_limit,
            "trading_enabled": config.trading_enabled,
            "warmup_bars": config.warmup_bars,
        },
        "risk": {
            "allow_leverage": risk.allow_leverage,
            "allow_live_trading": risk.allow_live_trading,
            "allow_shorting": risk.allow_shorting,
            "allowed_symbols": list(risk.allowed_symbols),
            "max_asset_weight": decimal_string(risk.max_asset_weight),
            "max_data_age_seconds": risk.max_data_age_seconds,
            "max_daily_loss_pct": decimal_string(risk.max_daily_loss_pct),
            "max_drawdown_pct": decimal_string(risk.max_drawdown_pct),
            "max_open_positions": risk.max_open_positions,
            "max_order_notional_weight": decimal_string(risk.max_order_notional_weight),
            "max_total_gross_exposure": decimal_string(risk.max_total_gross_exposure),
        },
    }


def _backtest_config(values: dict[str, Any]) -> BacktestConfig:
    if _string(values, "fee_model") != "notional_bps":
        raise ParityContractError("fee_model must be notional_bps")
    if _string(values, "slippage_model") != "adverse_bps":
        raise ParityContractError("slippage_model must be adverse_bps")
    return BacktestConfig(
        initial_cash=_decimal_float(values, "initial_cash"),
        fee_bps=_decimal_float(values, "fee_bps"),
        minimum_fee=_decimal_float(values, "minimum_fee"),
        slippage_bps=_decimal_float(values, "slippage_bps"),
        max_position_pct=_decimal_float(values, "max_position_pct"),
        max_total_exposure_pct=_decimal_float(values, "max_total_exposure_pct"),
        max_order_notional_pct=_decimal_float(values, "max_order_notional_pct"),
        max_daily_loss_pct=_decimal_float(values, "max_daily_loss_pct"),
        max_drawdown_pct=_decimal_float(values, "max_drawdown_pct"),
        max_open_positions=_integer(values, "max_open_positions"),
        warmup_bars=_integer(values, "warmup_bars"),
        data_age_seconds=_integer(values, "data_age_seconds"),
        trading_enabled=_boolean(values, "trading_enabled"),
        kill_switch_active=_boolean(values, "kill_switch_active"),
        execution_timing=_string(values, "execution_timing"),  # type: ignore[arg-type]
        quantity_precision=_integer(values, "quantity_precision"),
        money_precision=_integer(values, "money_precision"),
        strategy_history_limit=_integer(values, "strategy_history_limit"),
    )


def _risk_policy(values: dict[str, Any]) -> RiskPolicy:
    symbols = values.get("allowed_symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        raise ParityContractError("risk.allowed_symbols must be a non-empty string list")
    return RiskPolicy(
        allow_live_trading=_boolean(values, "allow_live_trading"),
        allow_shorting=_boolean(values, "allow_shorting"),
        allow_leverage=_boolean(values, "allow_leverage"),
        max_asset_weight=_decimal_float(values, "max_asset_weight"),
        max_total_gross_exposure=_decimal_float(values, "max_total_gross_exposure"),
        max_order_notional_weight=_decimal_float(values, "max_order_notional_weight"),
        max_daily_loss_pct=_decimal_float(values, "max_daily_loss_pct"),
        max_drawdown_pct=_decimal_float(values, "max_drawdown_pct"),
        max_data_age_seconds=_integer(values, "max_data_age_seconds"),
        max_open_positions=_integer(values, "max_open_positions"),
        allowed_symbols=tuple(symbols),
    )


def _validate_expected_outcome(trace: dict[str, Any], expected: dict[str, Any]) -> None:
    checks = {
        "bar_count": len(trace["bars"]),
        "fill_count": len(trace["fills"]),
        "risk_rejection_count": trace["summary"]["rejected_order_count"],
        "final_bar_creates_intent": trace["final_bar"]["creates_intent"],
        "final_bar_creates_fill": trace["final_bar"]["creates_fill"],
    }
    for key, actual in checks.items():
        if expected.get(key) != actual:
            raise ParityContractError(
                f"scenario expected {key}={expected.get(key)!r}, local oracle produced {actual!r}"
            )
    final_target = parse_decimal_string(
        expected.get("final_target_weight"),
        field="expected.final_target_weight",
    )
    actual_target = parse_decimal_string(
        trace["final_bar"]["target_weight"],
        field="final_bar.target_weight",
    )
    if final_target != actual_target:
        raise ParityContractError("scenario final target differs from local oracle")


def _object(values: dict[str, Any], key: str) -> dict[str, Any]:
    selected = values.get(key)
    if not isinstance(selected, dict):
        raise ParityContractError(f"{key} must be an object")
    return selected


def _string(values: dict[str, Any], key: str) -> str:
    selected = values.get(key)
    if not isinstance(selected, str) or not selected:
        raise ParityContractError(f"{key} must be a non-empty string")
    return selected


def _integer(values: dict[str, Any], key: str) -> int:
    selected = values.get(key)
    if type(selected) is not int:
        raise ParityContractError(f"{key} must be an integer")
    return selected


def _boolean(values: dict[str, Any], key: str) -> bool:
    selected = values.get(key)
    if type(selected) is not bool:
        raise ParityContractError(f"{key} must be a bool")
    return selected


def _decimal_float(values: dict[str, Any], key: str) -> float:
    return float(parse_decimal_string(values.get(key), field=key))


def _required_number(value: object, field: str) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ParityContractError(f"{field} must be present and numeric")
    return value


__all__ = ["build_local_parity_trace", "write_local_parity_trace"]
