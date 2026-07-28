"""Stable, atomic local report schemas for hypothetical simulation output."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot_lab import __version__
from trading_bot_lab.artifacts import atomic_write_csv, atomic_write_text, stable_csv_value
from trading_bot_lab.backtesting.engine import ENGINE_VERSION, BacktestConfig
from trading_bot_lab.domain import BacktestResult, RiskConfiguration, RiskStatus
from trading_bot_lab.risk import RiskPolicy

REPORT_SCHEMA_VERSION = "1.2.0"
DISCLAIMER = (
    "Hypothetical backtest for research only; not financial advice, not a profitability claim, "
    "and not evidence that future results will match simulated results."
)


def export_json_report(
    result: BacktestResult,
    config: BacktestConfig,
    path: str | Path,
    *,
    policy: RiskPolicy | None = None,
    warnings: tuple[str, ...] = (),
    generated_at: datetime | None = None,
) -> Path:
    """Atomically write a versioned JSON summary to a local artifact path."""

    _validate_report_contract(result, config, policy)
    generated = generated_at or datetime.now(UTC)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "package_version": __version__,
        "generated_at": generated.astimezone(UTC),
        "strategy_name": result.strategy_name,
        "strategy_configuration": dict(result.strategy_configuration),
        "session_id": result.session_id,
        "mode": "backtest",
        "input_filename": result.input_metadata.source,
        "input_content_sha256": result.input_metadata.content_sha256,
        "normalized_bars_sha256": result.input_metadata.bars_sha256,
        "input_data": asdict(result.input_metadata),
        "data_validation_warnings": [
            asdict(warning) for warning in result.warnings if warning.is_data_validation
        ],
        "simulation_warnings": [
            asdict(warning) for warning in result.warnings if not warning.is_data_validation
        ],
        "backtest_assumptions": asdict(result.assumptions),
        "risk_configuration": asdict(result.risk_configuration),
        "execution_timing": result.assumptions.execution_timing,
        "result_summary": asdict(result.summary),
        "benchmark_summaries": benchmark_payload(result),
        "halt_state": asdict(result.halt_state),
        "rejection_summary": rejection_summary(result),
        "risk_decisions": [asdict(decision) for decision in result.risk_decisions],
        "start_event_timestamp": result.summary.start_timestamp,
        "end_event_timestamp": result.summary.end_timestamp,
        "start_timestamp": result.summary.start_timestamp,
        "end_timestamp": result.summary.end_timestamp,
        "warnings": [warning.message for warning in result.warnings] + list(warnings),
        "disclaimer": DISCLAIMER,
    }
    serialized = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        default=_json_default,
        allow_nan=False,
    )
    return atomic_write_text(path, serialized)


def export_equity_csv(result: BacktestResult, path: str | Path) -> Path:
    """Atomically write the stable version-1.2 equity-curve schema."""

    fieldnames = [
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
    rows = (
        _stable_row(
            {
                "timestamp": point.timestamp.isoformat(),
                "close": point.close,
                "cash": point.cash,
                "quantity": point.quantity,
                "average_cost": point.average_cost,
                "position_market_value": point.position_market_value,
                "equity": point.equity,
                "start_of_day_equity": point.start_of_day_equity,
                "daily_pnl": point.daily_pnl,
                "peak_equity": point.peak_equity,
                "exposure": point.exposure_pct,
                "realized_pnl": point.realized_pnl,
                "unrealized_pnl": point.unrealized_pnl,
                "cumulative_fees": point.cumulative_fees,
                "cumulative_slippage": point.cumulative_slippage,
                "drawdown": point.drawdown,
                "halt_state": "halted" if point.halt_state.active else "active",
            }
        )
        for point in result.equity_curve
    )
    return atomic_write_csv(path, fieldnames, rows)


def export_trades_csv(result: BacktestResult, path: str | Path) -> Path:
    """Atomically write approved simulated fills and accounting deltas."""

    fieldnames = [
        "intent_id",
        "fill_id",
        "intent_timestamp",
        "fill_timestamp",
        "execution_phase",
        "symbol",
        "side",
        "quantity",
        "reference_price",
        "fill_price",
        "notional",
        "fee",
        "slippage_cost",
        "average_cost_after",
        "realized_pnl_delta",
        "resulting_cash",
        "resulting_quantity",
        "target_weight",
    ]
    rows = (
        _stable_row(
            {
                "intent_id": trade.fill.intent_id,
                "fill_id": trade.fill.fill_id,
                "intent_timestamp": trade.signal_timestamp.isoformat(),
                "fill_timestamp": trade.fill.timestamp.isoformat(),
                "execution_phase": trade.fill.execution_phase.value,
                "symbol": trade.fill.symbol,
                "side": trade.fill.side.value,
                "quantity": trade.fill.quantity,
                "reference_price": trade.fill.reference_price,
                "fill_price": trade.fill.execution_price,
                "notional": trade.notional,
                "fee": trade.fill.fee,
                "slippage_cost": trade.fill.slippage_cost,
                "average_cost_after": trade.average_cost_after,
                "realized_pnl_delta": trade.realized_pnl_delta,
                "resulting_cash": trade.resulting_cash,
                "resulting_quantity": trade.resulting_quantity,
                "target_weight": trade.target_weight,
            }
        )
        for trade in result.trades
    )
    return atomic_write_csv(path, fieldnames, rows)


def export_rejected_intents_csv(result: BacktestResult, path: str | Path) -> Path:
    """Atomically write rejected order-intent decisions only."""

    intents = {intent.intent_id: intent for intent in result.order_intents}
    fieldnames = [
        "timestamp",
        "intent_id",
        "symbol",
        "side",
        "quantity",
        "notional",
        "reasons",
    ]
    rows = (
        _stable_row(
            {
                "timestamp": decision.timestamp.isoformat(),
                "intent_id": decision.intent_id,
                "symbol": intents[decision.intent_id].symbol,
                "side": intents[decision.intent_id].side.value,
                "quantity": intents[decision.intent_id].quantity,
                "notional": intents[decision.intent_id].notional,
                "reasons": "|".join(reason.value for reason in decision.reasons),
            }
        )
        for decision in result.risk_decisions
        if decision.status is RiskStatus.REJECTED and decision.intent_id in intents
    )
    return atomic_write_csv(path, fieldnames, rows)


def export_risk_events_csv(result: BacktestResult, path: str | Path) -> Path:
    """Atomically write every intent and portfolio risk decision."""

    fieldnames = ["timestamp", "status", "intent_id", "reasons", "metrics"]
    rows = (
        {
            "timestamp": decision.timestamp.isoformat(),
            "status": decision.status.value,
            "intent_id": decision.intent_id or "",
            "reasons": "|".join(reason.value for reason in decision.reasons),
            "metrics": json.dumps(
                decision.metrics,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        }
        for decision in result.risk_decisions
    )
    return atomic_write_csv(path, fieldnames, rows)


def _validate_report_contract(
    result: BacktestResult,
    config: BacktestConfig,
    policy: RiskPolicy | None,
) -> None:
    if config != result.assumptions:
        raise ValueError("report BacktestConfig does not match the completed simulation")
    if (
        policy is not None
        and _effective_risk_configuration(
            policy,
            result.assumptions,
        )
        != result.risk_configuration
    ):
        raise ValueError("report RiskPolicy does not match the effective simulation policy")


def _effective_risk_configuration(
    policy: RiskPolicy,
    config: BacktestConfig,
) -> RiskConfiguration:
    return RiskConfiguration(
        allow_live_trading=policy.allow_live_trading,
        allow_shorting=policy.allow_shorting,
        allow_leverage=policy.allow_leverage,
        max_asset_weight=min(policy.max_asset_weight, config.max_position_pct),
        max_total_gross_exposure=min(
            policy.max_total_gross_exposure,
            config.max_total_exposure_pct,
        ),
        max_order_notional_weight=min(
            policy.max_order_notional_weight,
            config.max_order_notional_pct,
        ),
        max_daily_loss_pct=min(policy.max_daily_loss_pct, config.max_daily_loss_pct),
        max_drawdown_pct=min(policy.max_drawdown_pct, config.max_drawdown_pct),
        max_data_age_seconds=policy.max_data_age_seconds,
        max_open_positions=min(policy.max_open_positions, config.max_open_positions),
        allowed_symbols=policy.allowed_symbols,
    )


def benchmark_payload(result: BacktestResult) -> dict[str, Any]:
    """Return benchmark results plus clearly directed strategy differences."""

    strategy = result.summary
    buy_and_hold = result.benchmarks.buy_and_hold
    cash = result.benchmarks.cash
    return {
        "buy_and_hold": asdict(buy_and_hold),
        "cash": asdict(cash),
        "comparisons": {
            "strategy_minus_buy_and_hold": {
                "ending_equity": strategy.ending_equity - buy_and_hold.ending_equity,
                "total_return": strategy.total_return - buy_and_hold.total_return,
                "max_drawdown_positive_means_strategy_worse": (
                    strategy.max_drawdown - buy_and_hold.max_drawdown
                ),
            },
            "strategy_minus_cash": {
                "ending_equity": strategy.ending_equity - cash.ending_equity,
                "total_return": strategy.total_return - cash.total_return,
                "max_drawdown_positive_means_strategy_worse": (
                    strategy.max_drawdown - cash.max_drawdown
                ),
            },
        },
    }


def rejection_summary(result: BacktestResult) -> dict[str, Any]:
    """Return stable rejected-intent counts grouped by typed reason."""

    counts: dict[str, int] = {}
    rejected_count = 0
    for decision in result.risk_decisions:
        if decision.status is not RiskStatus.REJECTED or decision.intent_id is None:
            continue
        rejected_count += 1
        for reason in decision.reasons:
            counts[reason.value] = counts.get(reason.value, 0) + 1
    return {
        "rejected_intent_count": rejected_count,
        "counts_by_reason": dict(sorted(counts.items())),
    }


def _stable_row(row: dict[str, object]) -> dict[str, object]:
    return {key: stable_csv_value(value) for key, value in row.items()}


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = [
    "DISCLAIMER",
    "REPORT_SCHEMA_VERSION",
    "benchmark_payload",
    "export_equity_csv",
    "export_json_report",
    "export_rejected_intents_csv",
    "export_risk_events_csv",
    "export_trades_csv",
    "rejection_summary",
]
