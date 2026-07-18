"""Stable local report schemas for hypothetical simulation output."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot_lab.backtesting.engine import ENGINE_VERSION, BacktestConfig
from trading_bot_lab.domain import BacktestResult, RiskStatus
from trading_bot_lab.risk import RiskPolicy

REPORT_SCHEMA_VERSION = "1.0.0"
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
) -> Path:
    """Write a versioned JSON summary to an ignored local path."""

    report_path = _prepare_path(path)
    selected_policy = policy or RiskPolicy(allowed_symbols=(result.symbol,))
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(UTC),
        "strategy_name": result.strategy_name,
        "session_id": result.session_id,
        "mode": "backtest",
        "input_data": asdict(result.input_metadata),
        "data_validation_warnings": [asdict(warning) for warning in result.warnings],
        "backtest_assumptions": asdict(config),
        "risk_configuration": asdict(selected_policy),
        "result_summary": asdict(result.summary),
        "benchmark_summaries": _benchmark_payload(result),
        "halt_state": asdict(result.halt_state),
        "rejection_summary": _rejection_summary(result),
        "risk_decisions": [asdict(decision) for decision in result.risk_decisions],
        "start_timestamp": result.summary.start_timestamp,
        "end_timestamp": result.summary.end_timestamp,
        "warnings": [warning.message for warning in result.warnings] + list(warnings),
        "disclaimer": DISCLAIMER,
    }
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    return report_path


def export_equity_csv(result: BacktestResult, path: str | Path) -> Path:
    """Write the stable version-1 equity curve schema."""

    report_path = _prepare_path(path)
    fieldnames = [
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
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for point in result.equity_curve:
            writer.writerow(
                {
                    "timestamp": point.timestamp.isoformat(),
                    "close": point.close,
                    "cash": point.cash,
                    "quantity": point.quantity,
                    "average_cost": point.average_cost,
                    "position_market_value": point.position_market_value,
                    "equity": point.equity,
                    "exposure": point.exposure_pct,
                    "realized_pnl": point.realized_pnl,
                    "unrealized_pnl": point.unrealized_pnl,
                    "cumulative_fees": point.cumulative_fees,
                    "cumulative_slippage": point.cumulative_slippage,
                    "drawdown": point.drawdown,
                    "halt_state": "halted" if point.halt_state.active else "active",
                }
            )
    return report_path


def export_trades_csv(result: BacktestResult, path: str | Path) -> Path:
    """Write approved simulated fills and accounting deltas."""

    report_path = _prepare_path(path)
    fieldnames = [
        "intent_id",
        "timestamp",
        "symbol",
        "side",
        "quantity",
        "reference_price",
        "execution_price",
        "notional",
        "fee",
        "slippage_cost",
        "average_cost_after",
        "realized_pnl_delta",
        "target_weight",
    ]
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(
                {
                    "intent_id": trade.fill.intent_id,
                    "timestamp": trade.fill.timestamp.isoformat(),
                    "symbol": trade.fill.symbol,
                    "side": trade.fill.side.value,
                    "quantity": trade.fill.quantity,
                    "reference_price": trade.fill.reference_price,
                    "execution_price": trade.fill.execution_price,
                    "notional": trade.notional,
                    "fee": trade.fill.fee,
                    "slippage_cost": trade.fill.slippage_cost,
                    "average_cost_after": trade.average_cost_after,
                    "realized_pnl_delta": trade.realized_pnl_delta,
                    "target_weight": trade.target_weight,
                }
            )
    return report_path


def export_rejected_intents_csv(result: BacktestResult, path: str | Path) -> Path:
    """Write rejected order-intent decisions only."""

    report_path = _prepare_path(path)
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
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for decision in result.risk_decisions:
            if decision.status is not RiskStatus.REJECTED or decision.intent_id is None:
                continue
            intent = intents[decision.intent_id]
            writer.writerow(
                {
                    "timestamp": decision.timestamp.isoformat(),
                    "intent_id": decision.intent_id,
                    "symbol": intent.symbol,
                    "side": intent.side.value,
                    "quantity": intent.quantity,
                    "notional": intent.notional,
                    "reasons": "|".join(reason.value for reason in decision.reasons),
                }
            )
    return report_path


def _prepare_path(path: str | Path) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    return report_path


def _benchmark_payload(result: BacktestResult) -> dict[str, Any]:
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
                "max_drawdown": strategy.max_drawdown - buy_and_hold.max_drawdown,
            },
            "strategy_minus_cash": {
                "ending_equity": strategy.ending_equity - cash.ending_equity,
                "total_return": strategy.total_return - cash.total_return,
                "max_drawdown": strategy.max_drawdown - cash.max_drawdown,
            },
        },
    }


def _rejection_summary(result: BacktestResult) -> dict[str, Any]:
    counts: dict[str, int] = {}
    rejected_count = 0
    for decision in result.risk_decisions:
        if decision.status is not RiskStatus.REJECTED or decision.intent_id is None:
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
    "DISCLAIMER",
    "REPORT_SCHEMA_VERSION",
    "export_equity_csv",
    "export_json_report",
    "export_rejected_intents_csv",
    "export_trades_csv",
]
