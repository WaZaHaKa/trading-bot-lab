"""Strict offline comparison of local-oracle and LEAN parity traces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_bot_lab.parity.contract import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    DEFAULT_SCENARIO_PATH,
    TRACE_SCHEMA_VERSION,
    ParityContractError,
    load_contract_bundle,
    load_json_object,
    load_scenario_bundle,
    parse_decimal_string,
)

COMPARISON_DIMENSIONS = (
    "fixture_identity",
    "bar_visibility",
    "signal_timing",
    "intent_timing",
    "fill_timing",
    "trade_direction_and_count",
    "position_state",
    "fees",
    "slippage",
    "cash",
    "realized_unrealized_pnl",
    "equity",
    "exposure",
    "drawdown",
    "final_bar_behavior",
    "rejection_and_halt_state",
)


class ParityValidationError(ValueError):
    """Raised when a trace is malformed or not bound to the selected contract."""


class ParityMismatchError(AssertionError):
    """Raised when two valid observations diverge beyond the contract."""

    def __init__(self, differences: list[str]) -> None:
        self.differences = tuple(differences)
        grouped = _group_differences(differences)
        self.differences_by_dimension = {
            dimension: tuple(values) for dimension, values in grouped.items()
        }
        labelled: list[str] = []
        for dimension, values in grouped.items():
            labelled.extend(f"[{dimension}] {value}" for value in values)
        super().__init__("parity mismatch:\n- " + "\n- ".join(labelled))


@dataclass(frozen=True)
class ParityComparison:
    """Successful comparison summary without engine/account identifiers."""

    matched: bool
    scenario_id: str
    candidate_provenance: str
    bars_compared: int
    intents_compared: int
    fills_compared: int
    risk_decisions_compared: int
    tolerances: dict[str, str]
    dimensions: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "bars_compared": self.bars_compared,
            "candidate_provenance": self.candidate_provenance,
            "fills_compared": self.fills_compared,
            "intents_compared": self.intents_compared,
            "matched": self.matched,
            "dimensions": dict(sorted(self.dimensions.items())),
            "risk_decisions_compared": self.risk_decisions_compared,
            "scenario_id": self.scenario_id,
            "tolerances": dict(sorted(self.tolerances.items())),
        }


_TOP_FIELDS = {
    "schema_version",
    "contract",
    "provenance",
    "engine",
    "scenario",
    "assumptions",
    "strategy",
    "bars",
    "order_intents",
    "risk_decisions",
    "fills",
    "trades",
    "summary",
    "final_bar",
}
_CONTRACT_FIELDS = {
    "contract_name",
    "contract_version",
    "contract_sha256",
    "scenario_manifest_sha256",
    "scenario_schema_sha256",
    "trace_schema_sha256",
}
_SCENARIO_FIELDS = {
    "scenario_id",
    "fixture",
    "fixture_sha256",
    "normalized_bars_sha256",
    "symbol",
    "timeframe_seconds",
    "bar_count",
    "start_timestamp",
    "end_timestamp",
}
_BAR_EXACT = {"index", "timestamp", "symbol", "halted"}
_BAR_NUMERIC = {
    "open": "price",
    "high": "price",
    "low": "price",
    "close": "price",
    "volume": "quantity",
    "target_weight_for_next_bar": "ratio",
    "cash": "money",
    "quantity": "quantity",
    "average_cost": "price",
    "position_market_value": "money",
    "equity": "money",
    "start_of_day_equity": "money",
    "daily_pnl": "money",
    "peak_equity": "money",
    "exposure_pct": "ratio",
    "realized_pnl": "money",
    "unrealized_pnl": "money",
    "cumulative_fees": "money",
    "cumulative_slippage": "money",
    "drawdown": "ratio",
}
_INTENT_EXACT = {
    "index",
    "signal_timestamp",
    "execution_timestamp",
    "execution_phase",
    "symbol",
    "side",
}
_INTENT_NUMERIC = {
    "quantity": "quantity",
    "reference_price": "price",
    "estimated_execution_price": "price",
    "estimated_fee": "money",
    "target_weight": "ratio",
    "notional": "money",
}
_RISK_FIELDS = {"index", "timestamp", "intent_index", "status", "reasons", "metrics"}
_FILL_EXACT = {
    "index",
    "intent_index",
    "timestamp",
    "execution_phase",
    "symbol",
    "side",
}
_FILL_NUMERIC = {
    "quantity": "quantity",
    "reference_price": "price",
    "execution_price": "price",
    "fee": "money",
    "slippage_cost": "money",
}
_TRADE_EXACT = {
    "index",
    "fill_index",
    "signal_timestamp",
    "fill_timestamp",
    "symbol",
    "side",
}
_TRADE_NUMERIC = {
    "quantity": "quantity",
    "average_cost_after": "price",
    "realized_pnl_delta": "money",
    "resulting_cash": "money",
    "resulting_quantity": "quantity",
    "target_weight": "ratio",
}
_SUMMARY_EXACT = {
    "number_of_fills",
    "rejected_order_count",
    "risk_halt_triggered",
    "halt_reasons",
}
_SUMMARY_NUMERIC = {
    "starting_cash": "money",
    "ending_equity": "money",
    "total_return": "ratio",
    "max_drawdown": "ratio",
    "turnover": "ratio",
    "total_fees_paid": "money",
    "estimated_slippage_cost": "money",
    "average_exposure": "ratio",
    "max_exposure": "ratio",
    "realized_pnl": "money",
    "unrealized_pnl": "money",
}
_FINAL_EXACT = {"timestamp", "creates_intent", "creates_fill", "pending_signal_unfilled"}
_FINAL_NUMERIC = {"target_weight": "ratio"}


def compare_parity_files(
    local_trace_path: str | Path,
    candidate_trace_path: str | Path,
    *,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> ParityComparison:
    """Load two trace files and compare them without any network operation."""

    local, _ = load_json_object(local_trace_path)
    candidate, _ = load_json_object(candidate_trace_path)
    return compare_parity_traces(local, candidate, scenario_path=scenario_path)


def validate_parity_candidate_trace(
    candidate: dict[str, Any],
    *,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> None:
    """Validate one candidate against the selected immutable v1 contract."""

    try:
        contract = load_contract_bundle()
        scenario = load_scenario_bundle(scenario_path)
        _validate_trace(candidate, role="candidate", contract=contract, scenario=scenario)
    except ParityContractError as exc:
        raise ParityValidationError(str(exc)) from exc


def compare_parity_traces(
    local: dict[str, Any],
    candidate: dict[str, Any],
    *,
    scenario_path: str | Path = DEFAULT_SCENARIO_PATH,
) -> ParityComparison:
    """Compare one local oracle trace with one LEAN observation or labelled fixture."""

    try:
        contract = load_contract_bundle()
        scenario = load_scenario_bundle(scenario_path)
        _validate_trace(local, role="local", contract=contract, scenario=scenario)
        _validate_trace(candidate, role="candidate", contract=contract, scenario=scenario)
    except ParityContractError as exc:
        raise ParityValidationError(str(exc)) from exc

    tolerances = {
        name: parse_decimal_string(value, field=f"tolerances.{name}")
        for name, value in contract.contract["tolerances"].items()
    }
    differences: list[str] = []
    _compare_exact("contract", local["contract"], candidate["contract"], differences)
    _compare_exact("scenario", local["scenario"], candidate["scenario"], differences)
    _compare_exact("assumptions", local["assumptions"], candidate["assumptions"], differences)
    _compare_exact("strategy", local["strategy"], candidate["strategy"], differences)
    _compare_rows(
        "bars",
        local["bars"],
        candidate["bars"],
        exact_fields=_BAR_EXACT,
        numeric_fields=_BAR_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    _compare_rows(
        "order_intents",
        local["order_intents"],
        candidate["order_intents"],
        exact_fields=_INTENT_EXACT,
        numeric_fields=_INTENT_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    _compare_risk(local["risk_decisions"], candidate["risk_decisions"], tolerances, differences)
    _compare_rows(
        "fills",
        local["fills"],
        candidate["fills"],
        exact_fields=_FILL_EXACT,
        numeric_fields=_FILL_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    _compare_rows(
        "trades",
        local["trades"],
        candidate["trades"],
        exact_fields=_TRADE_EXACT,
        numeric_fields=_TRADE_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    _compare_record(
        "summary",
        local["summary"],
        candidate["summary"],
        exact_fields=_SUMMARY_EXACT,
        numeric_fields=_SUMMARY_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    _compare_record(
        "final_bar",
        local["final_bar"],
        candidate["final_bar"],
        exact_fields=_FINAL_EXACT,
        numeric_fields=_FINAL_NUMERIC,
        tolerances=tolerances,
        differences=differences,
    )
    if differences:
        raise ParityMismatchError(differences)
    return ParityComparison(
        matched=True,
        scenario_id=local["scenario"]["scenario_id"],
        candidate_provenance=candidate["provenance"],
        bars_compared=len(local["bars"]),
        intents_compared=len(local["order_intents"]),
        fills_compared=len(local["fills"]),
        risk_decisions_compared=len(local["risk_decisions"]),
        dimensions={dimension: "matched" for dimension in COMPARISON_DIMENSIONS},
        tolerances={key: str(value) for key, value in contract.contract["tolerances"].items()},
    )


def _validate_trace(
    trace: dict[str, Any],
    *,
    role: str,
    contract: Any,
    scenario: Any,
) -> None:
    _require_fields(trace, _TOP_FIELDS, role)
    if trace["schema_version"] != TRACE_SCHEMA_VERSION:
        raise ParityContractError(f"{role} trace has unsupported schema_version")
    provenance = trace["provenance"]
    if role == "local":
        if provenance != contract.contract["local_oracle_provenance"]:
            raise ParityContractError("local trace has invalid provenance")
    elif provenance not in contract.contract["accepted_candidate_provenance"]:
        raise ParityContractError("candidate trace has invalid provenance")

    descriptor = _dict(trace, "contract", role)
    _require_fields(descriptor, _CONTRACT_FIELDS, f"{role}.contract")
    expected_descriptor = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "contract_sha256": contract.contract_sha256,
        "scenario_manifest_sha256": scenario.manifest_sha256,
        "scenario_schema_sha256": contract.scenario_schema_sha256,
        "trace_schema_sha256": contract.trace_schema_sha256,
    }
    if descriptor != expected_descriptor:
        raise ParityContractError(f"{role} trace contract hashes or versions are invalid")

    engine = _dict(trace, "engine", role)
    _require_fields(engine, {"name", "version"}, f"{role}.engine")
    for field in ("name", "version"):
        if not isinstance(engine[field], str) or not engine[field]:
            raise ParityContractError(f"{role}.engine.{field} must be a non-empty string")

    scenario_trace = _dict(trace, "scenario", role)
    _require_fields(scenario_trace, _SCENARIO_FIELDS, f"{role}.scenario")
    manifest = scenario.manifest
    expected_scenario = {
        "scenario_id": manifest["scenario_id"],
        "fixture": manifest["fixture"],
        "fixture_sha256": scenario.fixture_sha256,
        "symbol": manifest["symbol"],
        "timeframe_seconds": manifest["timeframe_seconds"],
    }
    for key, value in expected_scenario.items():
        if scenario_trace[key] != value:
            raise ParityContractError(f"{role}.scenario.{key} is not bound to the manifest")
    _validate_hash(scenario_trace["normalized_bars_sha256"], f"{role}.scenario.normalized")
    if type(scenario_trace["bar_count"]) is not int or scenario_trace["bar_count"] <= 0:
        raise ParityContractError(f"{role}.scenario.bar_count must be a positive integer")
    _validate_timestamp(scenario_trace["start_timestamp"], f"{role}.scenario.start_timestamp")
    _validate_timestamp(scenario_trace["end_timestamp"], f"{role}.scenario.end_timestamp")

    assumptions = _dict(trace, "assumptions", role)
    _validate_assumptions(assumptions, role)
    strategy = _dict(trace, "strategy", role)
    _validate_strategy(strategy, role)

    bars = _list(trace, "bars", role)
    intents = _list(trace, "order_intents", role)
    risk = _list(trace, "risk_decisions", role)
    fills = _list(trace, "fills", role)
    trades = _list(trace, "trades", role)
    _validate_rows(bars, _BAR_EXACT, _BAR_NUMERIC, f"{role}.bars")
    _validate_rows(intents, _INTENT_EXACT, _INTENT_NUMERIC, f"{role}.order_intents")
    _validate_risk_rows(risk, f"{role}.risk_decisions")
    _validate_rows(fills, _FILL_EXACT, _FILL_NUMERIC, f"{role}.fills")
    _validate_rows(trades, _TRADE_EXACT, _TRADE_NUMERIC, f"{role}.trades")

    summary = _dict(trace, "summary", role)
    _validate_record(summary, _SUMMARY_EXACT, _SUMMARY_NUMERIC, f"{role}.summary")
    _validate_summary(summary, f"{role}.summary")
    final = _dict(trace, "final_bar", role)
    _validate_record(final, _FINAL_EXACT, _FINAL_NUMERIC, f"{role}.final_bar")
    _validate_final(final, f"{role}.final_bar")
    _validate_internal_consistency(trace, role)


def _validate_assumptions(assumptions: dict[str, Any], role: str) -> None:
    _require_fields(assumptions, {"backtest", "risk"}, f"{role}.assumptions")
    backtest = _dict(assumptions, "backtest", f"{role}.assumptions")
    risk = _dict(assumptions, "risk", f"{role}.assumptions")
    backtest_decimal = {
        "initial_cash",
        "fee_bps",
        "minimum_fee",
        "slippage_bps",
        "max_position_pct",
        "max_total_exposure_pct",
        "max_order_notional_pct",
        "max_daily_loss_pct",
        "max_drawdown_pct",
    }
    backtest_integer = {
        "max_open_positions",
        "warmup_bars",
        "data_age_seconds",
        "quantity_precision",
        "money_precision",
        "strategy_history_limit",
    }
    backtest_boolean = {"trading_enabled", "kill_switch_active"}
    _require_fields(
        backtest,
        backtest_decimal
        | backtest_integer
        | backtest_boolean
        | {"execution_timing", "fee_model", "slippage_model"},
        f"{role}.assumptions.backtest",
    )
    for field in backtest_decimal:
        parse_decimal_string(backtest[field], field=f"{role}.assumptions.backtest.{field}")
    for field in backtest_integer:
        if type(backtest[field]) is not int:
            raise ParityContractError(f"{role}.assumptions.backtest.{field} must be an integer")
    for field in backtest_boolean:
        if type(backtest[field]) is not bool:
            raise ParityContractError(f"{role}.assumptions.backtest.{field} must be a bool")
    if backtest["execution_timing"] != "next_bar_open":
        raise ParityContractError(f"{role} trace must use next_bar_open")
    if backtest["fee_model"] != "notional_bps":
        raise ParityContractError(f"{role} trace must use notional_bps fees")
    if backtest["slippage_model"] != "adverse_bps":
        raise ParityContractError(f"{role} trace must use adverse_bps slippage")

    risk_decimal = {
        "max_asset_weight",
        "max_total_gross_exposure",
        "max_order_notional_weight",
        "max_daily_loss_pct",
        "max_drawdown_pct",
    }
    risk_integer = {"max_data_age_seconds", "max_open_positions"}
    risk_boolean = {"allow_live_trading", "allow_shorting", "allow_leverage"}
    _require_fields(
        risk,
        risk_decimal | risk_integer | risk_boolean | {"allowed_symbols"},
        f"{role}.assumptions.risk",
    )
    for field in risk_decimal:
        parse_decimal_string(risk[field], field=f"{role}.assumptions.risk.{field}")
    for field in risk_integer:
        if type(risk[field]) is not int:
            raise ParityContractError(f"{role}.assumptions.risk.{field} must be an integer")
    for field in risk_boolean:
        if type(risk[field]) is not bool or risk[field]:
            raise ParityContractError(f"{role}.assumptions.risk.{field} must remain false")
    symbols = risk["allowed_symbols"]
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        raise ParityContractError(f"{role}.assumptions.risk.allowed_symbols is invalid")


def _validate_strategy(strategy: dict[str, Any], role: str) -> None:
    _require_fields(strategy, {"name", "configuration"}, f"{role}.strategy")
    if strategy["name"] != "moving_average":
        raise ParityContractError(f"{role}.strategy.name must be moving_average")
    configuration = _dict(strategy, "configuration", f"{role}.strategy")
    _require_fields(
        configuration,
        {"fast_window", "slow_window", "target_weight"},
        f"{role}.strategy.configuration",
    )
    if (
        type(configuration["fast_window"]) is not int
        or type(configuration["slow_window"]) is not int
    ):
        raise ParityContractError(f"{role} strategy windows must be integers")
    parse_decimal_string(
        configuration["target_weight"],
        field=f"{role}.strategy.configuration.target_weight",
    )


def _validate_rows(
    rows: list[Any],
    exact_fields: set[str],
    numeric_fields: dict[str, str],
    path: str,
) -> None:
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ParityContractError(f"{path}[{index}] must be an object")
        _validate_record(raw, exact_fields, numeric_fields, f"{path}[{index}]")
        for field in exact_fields:
            value = raw[field]
            if field in {"index", "intent_index", "fill_index"}:
                if type(value) is not int:
                    raise ParityContractError(f"{path}[{index}].{field} must be an integer")
            elif field == "halted":
                if type(value) is not bool:
                    raise ParityContractError(f"{path}[{index}].halted must be a bool")
            elif not isinstance(value, str) or not value:
                raise ParityContractError(f"{path}[{index}].{field} must be a non-empty string")
        if raw["index"] != index:
            raise ParityContractError(f"{path}[{index}].index must be sequential")
        for field in exact_fields:
            if "timestamp" in field:
                _validate_timestamp(raw[field], f"{path}[{index}].{field}")
        if "side" in exact_fields and raw["side"] not in {"buy", "sell"}:
            raise ParityContractError(f"{path}[{index}].side is invalid")
        if "execution_phase" in exact_fields and raw["execution_phase"] != "open":
            raise ParityContractError(f"{path}[{index}] must execute at open")


def _validate_record(
    record: dict[str, Any],
    exact_fields: set[str],
    numeric_fields: dict[str, str],
    path: str,
) -> None:
    _require_fields(record, exact_fields | set(numeric_fields), path)
    for field in numeric_fields:
        parse_decimal_string(record[field], field=f"{path}.{field}")


def _validate_risk_rows(rows: list[Any], path: str) -> None:
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ParityContractError(f"{path}[{index}] must be an object")
        _require_fields(raw, _RISK_FIELDS, f"{path}[{index}]")
        if raw["index"] != index:
            raise ParityContractError(f"{path}[{index}].index must be sequential")
        _validate_timestamp(raw["timestamp"], f"{path}[{index}].timestamp")
        if raw["intent_index"] is not None and type(raw["intent_index"]) is not int:
            raise ParityContractError(f"{path}[{index}].intent_index must be integer or null")
        if raw["status"] not in {"approved", "rejected"}:
            raise ParityContractError(f"{path}[{index}].status is invalid")
        if not isinstance(raw["reasons"], list) or any(
            not isinstance(reason, str) for reason in raw["reasons"]
        ):
            raise ParityContractError(f"{path}[{index}].reasons must be a string list")
        metrics = raw["metrics"]
        if not isinstance(metrics, dict) or any(not isinstance(key, str) for key in metrics):
            raise ParityContractError(f"{path}[{index}].metrics must be an object")
        for key, value in metrics.items():
            parse_decimal_string(value, field=f"{path}[{index}].metrics.{key}")
        if raw["status"] == "approved" and raw["reasons"]:
            raise ParityContractError(
                f"{path}[{index}] approved decisions must not contain reasons"
            )
        if raw["status"] == "rejected" and not raw["reasons"]:
            raise ParityContractError(f"{path}[{index}] rejected decisions must contain reasons")


def _validate_summary(summary: dict[str, Any], path: str) -> None:
    for field in ("number_of_fills", "rejected_order_count"):
        if type(summary[field]) is not int or summary[field] < 0:
            raise ParityContractError(f"{path}.{field} must be a non-negative integer")
    if type(summary["risk_halt_triggered"]) is not bool:
        raise ParityContractError(f"{path}.risk_halt_triggered must be a bool")
    reasons = summary["halt_reasons"]
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise ParityContractError(f"{path}.halt_reasons must be a string list")


def _validate_final(final: dict[str, Any], path: str) -> None:
    _validate_timestamp(final["timestamp"], f"{path}.timestamp")
    for field in ("creates_intent", "creates_fill", "pending_signal_unfilled"):
        if type(final[field]) is not bool:
            raise ParityContractError(f"{path}.{field} must be a bool")


def _validate_internal_consistency(trace: dict[str, Any], role: str) -> None:
    bars = trace["bars"]
    intents = trace["order_intents"]
    fills = trace["fills"]
    trades = trace["trades"]
    risk = trace["risk_decisions"]
    scenario = trace["scenario"]
    summary = trace["summary"]
    final = trace["final_bar"]
    if len(bars) != scenario["bar_count"]:
        raise ParityContractError(f"{role} bar count disagrees with scenario")
    if not bars:
        raise ParityContractError(f"{role} trace must contain bars")
    if (
        bars[0]["timestamp"] != scenario["start_timestamp"]
        or bars[-1]["timestamp"] != scenario["end_timestamp"]
    ):
        raise ParityContractError(f"{role} bar interval disagrees with scenario")
    if any(bar["symbol"] != scenario["symbol"] for bar in bars):
        raise ParityContractError(f"{role} bar symbols disagree with scenario")
    if summary["number_of_fills"] != len(fills) or len(fills) != len(trades):
        raise ParityContractError(f"{role} fill/trade counts are inconsistent")
    rejected = sum(
        decision["status"] == "rejected" and decision["intent_index"] is not None
        for decision in risk
    )
    if summary["rejected_order_count"] != rejected:
        raise ParityContractError(f"{role} rejection count is inconsistent")
    if any(fill["intent_index"] < 0 or fill["intent_index"] >= len(intents) for fill in fills):
        raise ParityContractError(f"{role} fill refers to a missing intent")
    if any(trade["fill_index"] < 0 or trade["fill_index"] >= len(fills) for trade in trades):
        raise ParityContractError(f"{role} trade refers to a missing fill")
    if any(
        decision["intent_index"] is not None
        and (decision["intent_index"] < 0 or decision["intent_index"] >= len(intents))
        for decision in risk
    ):
        raise ParityContractError(f"{role} risk decision refers to a missing intent")
    last_timestamp = bars[-1]["timestamp"]
    creates_intent = any(intent["signal_timestamp"] == last_timestamp for intent in intents)
    creates_fill = any(trade["signal_timestamp"] == last_timestamp for trade in trades)
    if final != {
        "timestamp": last_timestamp,
        "target_weight": bars[-1]["target_weight_for_next_bar"],
        "creates_intent": creates_intent,
        "creates_fill": creates_fill,
        "pending_signal_unfilled": not creates_intent and not creates_fill,
    }:
        raise ParityContractError(f"{role} final-bar semantics are internally inconsistent")


def _compare_rows(
    path: str,
    expected: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    exact_fields: set[str],
    numeric_fields: dict[str, str],
    tolerances: dict[str, Decimal],
    differences: list[str],
) -> None:
    if len(expected) != len(candidate):
        differences.append(f"{path} count expected {len(expected)}, observed {len(candidate)}")
        return
    for index, (left, right) in enumerate(zip(expected, candidate, strict=True)):
        _compare_record(
            f"{path}[{index}]",
            left,
            right,
            exact_fields=exact_fields,
            numeric_fields=numeric_fields,
            tolerances=tolerances,
            differences=differences,
        )


def _compare_record(
    path: str,
    expected: dict[str, Any],
    candidate: dict[str, Any],
    *,
    exact_fields: set[str],
    numeric_fields: dict[str, str],
    tolerances: dict[str, Decimal],
    differences: list[str],
) -> None:
    for field in sorted(exact_fields):
        _compare_exact(f"{path}.{field}", expected[field], candidate[field], differences)
    for field, category in sorted(numeric_fields.items()):
        _compare_decimal(
            f"{path}.{field}",
            expected[field],
            candidate[field],
            tolerance=tolerances[category],
            differences=differences,
        )


def _compare_risk(
    expected: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    tolerances: dict[str, Decimal],
    differences: list[str],
) -> None:
    if len(expected) != len(candidate):
        differences.append(
            f"risk_decisions count expected {len(expected)}, observed {len(candidate)}"
        )
        return
    for index, (left, right) in enumerate(zip(expected, candidate, strict=True)):
        path = f"risk_decisions[{index}]"
        for field in ("index", "timestamp", "intent_index", "status", "reasons"):
            _compare_exact(f"{path}.{field}", left[field], right[field], differences)
        if set(left["metrics"]) != set(right["metrics"]):
            differences.append(
                f"{path}.metrics keys expected {sorted(left['metrics'])}, "
                f"observed {sorted(right['metrics'])}"
            )
            continue
        for key in sorted(left["metrics"]):
            category = "money" if key in {"projected_cash", "projected_equity"} else "ratio"
            _compare_decimal(
                f"{path}.metrics.{key}",
                left["metrics"][key],
                right["metrics"][key],
                tolerance=tolerances[category],
                differences=differences,
            )


def _compare_exact(path: str, expected: object, candidate: object, differences: list[str]) -> None:
    if expected != candidate:
        differences.append(f"{path} expected {expected!r}, observed {candidate!r}")


def _compare_decimal(
    path: str,
    expected: str,
    candidate: str,
    *,
    tolerance: Decimal,
    differences: list[str],
) -> None:
    left = parse_decimal_string(expected, field=f"expected.{path}")
    right = parse_decimal_string(candidate, field=f"candidate.{path}")
    difference = abs(left - right)
    if difference > tolerance:
        differences.append(
            f"{path} expected {expected}, observed {candidate}, "
            f"difference {difference} exceeds tolerance {tolerance}"
        )


def _require_fields(values: dict[str, Any], required: set[str], path: str) -> None:
    if set(values) != required:
        missing = sorted(required - set(values))
        unexpected = sorted(set(values) - required)
        raise ParityContractError(
            f"{path} fields differ from schema; missing={missing}, unexpected={unexpected}"
        )


def _dict(values: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    selected = values.get(key)
    if not isinstance(selected, dict):
        raise ParityContractError(f"{path}.{key} must be an object")
    return selected


def _list(values: dict[str, Any], key: str, path: str) -> list[Any]:
    selected = values.get(key)
    if not isinstance(selected, list):
        raise ParityContractError(f"{path}.{key} must be an array")
    return selected


def _validate_hash(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ParityContractError(f"{field} must be a lowercase SHA-256")


def _validate_timestamp(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ParityContractError(f"{field} must be an ISO-8601 UTC string")
    try:
        selected = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ParityContractError(f"{field} must be an ISO-8601 UTC string") from exc
    offset = selected.utcoffset()
    if selected.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ParityContractError(f"{field} must include a UTC offset")


def _group_differences(differences: list[str]) -> dict[str, list[str]]:
    grouped = {dimension: [] for dimension in COMPARISON_DIMENSIONS}
    for difference in differences:
        grouped[_classify_difference(difference)].append(difference)
    return {dimension: values for dimension, values in grouped.items() if values}


def _classify_difference(difference: str) -> str:
    path = difference.split(" expected", maxsplit=1)[0]
    if path.startswith(("contract", "scenario", "assumptions")):
        return "fixture_identity"
    if path.startswith("strategy"):
        return "signal_timing"
    if path.startswith("risk_decisions"):
        return "rejection_and_halt_state"
    if path.startswith("final_bar"):
        return "final_bar_behavior"

    if path.startswith("bars count"):
        return "bar_visibility"
    if path.startswith("order_intents count"):
        return "intent_timing"
    if path.startswith(("fills count", "trades count")):
        return "trade_direction_and_count"

    field = path.rsplit(".", maxsplit=1)[-1]
    if path.startswith("bars"):
        return {
            "average_cost": "position_state",
            "cash": "cash",
            "cumulative_fees": "fees",
            "cumulative_slippage": "slippage",
            "daily_pnl": "realized_unrealized_pnl",
            "drawdown": "drawdown",
            "equity": "equity",
            "exposure_pct": "exposure",
            "halted": "rejection_and_halt_state",
            "peak_equity": "equity",
            "position_market_value": "position_state",
            "quantity": "position_state",
            "realized_pnl": "realized_unrealized_pnl",
            "start_of_day_equity": "equity",
            "target_weight_for_next_bar": "signal_timing",
            "unrealized_pnl": "realized_unrealized_pnl",
        }.get(field, "bar_visibility")
    if path.startswith("order_intents"):
        return {
            "estimated_execution_price": "intent_timing",
            "estimated_fee": "fees",
            "execution_phase": "intent_timing",
            "execution_timestamp": "intent_timing",
            "notional": "position_state",
            "quantity": "position_state",
            "reference_price": "intent_timing",
            "signal_timestamp": "signal_timing",
            "target_weight": "signal_timing",
        }.get(field, "trade_direction_and_count")
    if path.startswith("fills"):
        return {
            "execution_phase": "fill_timing",
            "execution_price": "slippage",
            "fee": "fees",
            "quantity": "position_state",
            "reference_price": "fill_timing",
            "slippage_cost": "slippage",
            "timestamp": "fill_timing",
        }.get(field, "trade_direction_and_count")
    if path.startswith("trades"):
        return {
            "average_cost_after": "position_state",
            "fill_timestamp": "fill_timing",
            "quantity": "position_state",
            "realized_pnl_delta": "realized_unrealized_pnl",
            "resulting_cash": "cash",
            "resulting_quantity": "position_state",
            "signal_timestamp": "signal_timing",
            "target_weight": "signal_timing",
        }.get(field, "trade_direction_and_count")
    if path.startswith("summary"):
        return {
            "average_exposure": "exposure",
            "ending_equity": "equity",
            "estimated_slippage_cost": "slippage",
            "halt_reasons": "rejection_and_halt_state",
            "max_drawdown": "drawdown",
            "max_exposure": "exposure",
            "number_of_fills": "trade_direction_and_count",
            "realized_pnl": "realized_unrealized_pnl",
            "rejected_order_count": "rejection_and_halt_state",
            "risk_halt_triggered": "rejection_and_halt_state",
            "starting_cash": "cash",
            "total_fees_paid": "fees",
            "total_return": "equity",
            "turnover": "exposure",
            "unrealized_pnl": "realized_unrealized_pnl",
        }.get(field, "fixture_identity")
    return "fixture_identity"


__all__ = [
    "COMPARISON_DIMENSIONS",
    "ParityComparison",
    "ParityMismatchError",
    "ParityValidationError",
    "compare_parity_files",
    "compare_parity_traces",
    "validate_parity_candidate_trace",
]
