"""Strict offline import of QuantConnect Download Results JSON."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, DecimalException
from hashlib import sha256
from pathlib import Path
from typing import Any

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.parity.contract import deterministic_json, load_json_object
from trading_bot_lab.walk_forward.contract import (
    CONTRACT_DIRECTORY,
    FOLD_IDS,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ProtocolBundle,
    WalkForwardContractError,
    fold_by_id,
    load_protocol_bundle,
)
from trading_bot_lab.walk_forward.observation import canonical_decimal

RESULT_SCHEMA_VERSION = "1.0.0"
RESULT_SOURCE_FORMAT = "quantconnect_result_json"
RESULT_RECORD_TYPE = "walk_forward_result_observation"
RESULT_AGGREGATE_RECORD_TYPE = "walk_forward_result_aggregate"
RESULT_OBSERVATION_SCHEMA_PATH = CONTRACT_DIRECTORY / "result-observation.schema.json"
RESULT_AGGREGATE_SCHEMA_PATH = CONTRACT_DIRECTORY / "result-aggregate-record.schema.json"
SOURCE_VERIFICATION_ATTESTATION = (
    "selected_private_cloud_project_source_and_public_configuration_verified_"
    "against_merged_repository_before_execution"
)

_MAX_INPUT_RESULT_BYTES = 8 * 1024 * 1024
_MAX_RESULT_OBSERVATION_BYTES = 128 * 1024
_MAX_RESULT_AGGREGATE_BYTES = 1024 * 1024
_MAX_UNTRUSTED_NODES = 100_000
_MAX_DECIMAL_TEXT_CHARACTERS = 128
_MAX_DECIMAL_ADJUSTED_EXPONENT = 100
_MAX_ORDERS = 10_000
_MAX_CHART_POINTS = 100_000
_ROUNDED_RATIO_TOLERANCE = Decimal("0.0005")
_ROUNDED_CURRENCY_TOLERANCE = Decimal("0.01")
_BENCHMARK_BOUNDARY_TOLERANCE = timedelta(days=7)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMAIL = re.compile(r"(?i)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_-]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
)
_STALE_PROJECT = re.compile(r"(?i)(?:^|[/\\])WalkForwardMovingAverageV1 1(?:$|[/\\])")
_UNAVAILABLE_FIELDS = (
    "algorithm_risk_halt_state",
    "engine_version",
    "estimated_slippage_usd",
    "rejected_order_count",
)
_ORDER_EVENT_DETAIL_UNAVAILABLE = "order_event_detail"
_ORDER_VALIDATION_SOURCES = frozenset({"completed_orders", "order_events"})
_FEE_VALIDATION_SOURCES = frozenset({"order_events", "overview_runtime_rounded"})
_FEE_PRECISIONS = frozenset({"order_event_amount_precision", "rounded_to_cent"})
_OUTPUT_PRIVATE_KEYS = frozenset(
    {
        "account_id",
        "backtest_id",
        "cloud_id",
        "compile_id",
        "credential",
        "credentials",
        "email",
        "hostname",
        "machine_path",
        "node_id",
        "organization_id",
        "owner",
        "project_id",
        "raw_order_ids",
        "session_id",
        "token",
        "url",
        "user_id",
    }
)


class WalkForwardResultError(ValueError):
    """Raised when official result JSON is unsafe, stale, or contradictory."""


def parse_result_json(
    input_result: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Validate and sanitize one QuantConnect Download Results JSON file."""

    selected_bundle = bundle or load_protocol_bundle()
    source = Path(input_result)
    _reject_path_links(source, "input result")
    _require_regular_file(source, "input result", _MAX_INPUT_RESULT_BYTES)
    payload, _raw = _load_bounded_json_object(source, _MAX_INPUT_RESULT_BYTES, "input result")
    _scan_input_for_stale_project(payload)
    return _normalize_download_result(payload, selected_bundle)


def extract_result_json(
    input_result: str | Path,
    output_path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> Path:
    """Write one sanitized official-result observation as deterministic JSON."""

    source = Path(input_result)
    destination = Path(output_path)
    if _path_identity(source) == _path_identity(destination):
        raise WalkForwardResultError("input result and output observation must differ")
    observation = parse_result_json(source, bundle=bundle)
    return _safe_atomic_json(destination, deterministic_json(observation))


def normalize_result_observation(
    payload: Mapping[str, object],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Recompute and verify a normalized official-result observation."""

    selected_bundle = bundle or load_protocol_bundle()
    if not isinstance(payload, Mapping):
        raise WalkForwardResultError("result observation must be an object")
    _scan_normalized_output(payload, "result observation")
    _require_exact_fields(
        payload,
        {
            "configuration",
            "evaluation_end",
            "evaluation_start",
            "fold_id",
            "metrics",
            "orders",
            "protocol",
            "record_type",
            "schema_version",
            "source",
            "source_format",
            "state",
        },
        "result observation",
    )
    if payload["schema_version"] != RESULT_SCHEMA_VERSION:
        raise WalkForwardResultError("result observation schema version is unsupported")
    if payload["record_type"] != RESULT_RECORD_TYPE:
        raise WalkForwardResultError("result observation record type is unsupported")
    if payload["source_format"] != RESULT_SOURCE_FORMAT:
        raise WalkForwardResultError("result observation source format is unsupported")

    fold_id = _nonempty_string(payload["fold_id"], "result observation.fold_id")
    try:
        fold = fold_by_id(selected_bundle, fold_id)
    except WalkForwardContractError as exc:
        raise WalkForwardResultError("unknown walk-forward result fold") from exc
    if payload["evaluation_start"] != fold.evaluation_start:
        raise WalkForwardResultError("result observation start date differs from its fold")
    if payload["evaluation_end"] != fold.evaluation_end:
        raise WalkForwardResultError("result observation end date differs from its fold")

    expected_protocol = {
        "content_sha256": selected_bundle.manifest_sha256,
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
    }
    if dict(_mapping(payload["protocol"], "result observation.protocol")) != expected_protocol:
        raise WalkForwardResultError("result observation protocol identity has drifted")
    expected_source = _source_identity(selected_bundle)
    if dict(_mapping(payload["source"], "result observation.source")) != expected_source:
        raise WalkForwardResultError("result observation source identity has drifted")

    expected_name = f"wf-v1-{fold.fold_id}"
    expected_configuration = {
        "account_currency": "USD",
        "account_type": "cash",
        "name": expected_name,
        "out_of_sample_days": 0,
        "parameters": {"fold-id": fold.fold_id, "optimization-mode": "false"},
    }
    if (
        dict(_mapping(payload["configuration"], "result observation.configuration"))
        != expected_configuration
    ):
        raise WalkForwardResultError("result observation configuration has drifted")
    if dict(_mapping(payload["state"], "result observation.state")) != {
        "completion_status": "completed"
    }:
        raise WalkForwardResultError("result observation state must be completed")

    orders = _normalize_output_orders(payload["orders"])
    metrics = _normalize_output_metrics(
        payload["metrics"], order_validation_source=orders["order_validation_source"]
    )
    return {
        "configuration": expected_configuration,
        "evaluation_end": fold.evaluation_end,
        "evaluation_start": fold.evaluation_start,
        "fold_id": fold.fold_id,
        "metrics": metrics,
        "orders": orders,
        "protocol": expected_protocol,
        "record_type": RESULT_RECORD_TYPE,
        "schema_version": RESULT_SCHEMA_VERSION,
        "source": expected_source,
        "source_format": RESULT_SOURCE_FORMAT,
        "state": {"completion_status": "completed"},
    }


def load_result_observation(
    path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load one deterministic normalized official-result observation."""

    selected = Path(path)
    _reject_path_links(selected, "result observation")
    _require_regular_file(selected, "result observation", _MAX_RESULT_OBSERVATION_BYTES)
    payload, raw = _load_bounded_json_object(
        selected, _MAX_RESULT_OBSERVATION_BYTES, "result observation"
    )
    normalized = normalize_result_observation(payload, bundle=bundle)
    if raw != deterministic_json(normalized).encode("utf-8"):
        raise WalkForwardResultError(
            "result observation must use sorted UTF-8 JSON with LF and a final newline"
        )
    return normalized


def aggregate_result_observations(
    observations: Sequence[Mapping[str, object]],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Build the exact-five descriptive aggregate for official-result observations."""

    selected_bundle = bundle or load_protocol_bundle()
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise WalkForwardResultError("result observations must be an array")
    if len(observations) != len(FOLD_IDS):
        raise WalkForwardResultError("result aggregation requires the exact five folds")
    normalized = [
        normalize_result_observation(item, bundle=selected_bundle) for item in observations
    ]
    fold_ids = [item["fold_id"] for item in normalized]
    if len(set(fold_ids)) != len(fold_ids):
        raise WalkForwardResultError("result aggregation contains a duplicate fold")
    if set(fold_ids) != set(FOLD_IDS):
        raise WalkForwardResultError("result aggregation requires the exact five folds")
    by_id = {item["fold_id"]: item for item in normalized}
    ordered = [by_id[fold_id] for fold_id in FOLD_IDS]

    returns = [_derived_metric(item, "total_return") for item in ordered]
    benchmark_returns = [_derived_metric(item, "benchmark_return") for item in ordered]
    excess_returns = [_derived_metric(item, "excess_return") for item in ordered]
    drawdowns = [_reported_metric(item, "maximum_drawdown") for item in ordered]
    fees = [_reported_metric(item, "total_fees_usd") for item in ordered]
    sharpes = [_reported_metric(item, "sharpe_ratio") for item in ordered]
    sortinos = [_reported_metric(item, "sortino_ratio") for item in ordered]
    probabilistic = [_reported_metric(item, "probabilistic_sharpe_ratio") for item in ordered]
    return {
        "contract_status": "walk_forward_result_contract_complete",
        "fold_results": ordered,
        "identities": {
            "aggregate_schema_sha256": _schema_sha256(
                RESULT_AGGREGATE_SCHEMA_PATH, "result aggregate schema"
            ),
            "observation_schema_sha256": _schema_sha256(
                RESULT_OBSERVATION_SCHEMA_PATH, "result observation schema"
            ),
            "protocol_manifest_sha256": selected_bundle.manifest_sha256,
            "project_source_sha256": selected_bundle.project_source_sha256,
            "public_configuration_sha256": selected_bundle.public_configuration_sha256,
        },
        "protocol_name": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "record_type": RESULT_AGGREGATE_RECORD_TYPE,
        "schema_version": RESULT_SCHEMA_VERSION,
        "source_formats": [RESULT_SOURCE_FORMAT],
        "summary": {
            "benchmark_beating_fold_count": sum(value > 0 for value in excess_returns),
            "completed_fold_count": len(ordered),
            "median_benchmark_return": canonical_decimal(_median(benchmark_returns)),
            "median_excess_return": canonical_decimal(_median(excess_returns)),
            "median_probabilistic_sharpe_ratio": canonical_decimal(_median(probabilistic)),
            "median_sharpe_ratio": canonical_decimal(_median(sharpes)),
            "median_sortino_ratio": canonical_decimal(_median(sortinos)),
            "median_strategy_return": canonical_decimal(_median(returns)),
            "positive_return_fold_count": sum(value > 0 for value in returns),
            "total_fees_usd": canonical_decimal(_exact_decimal_sum(fees)),
            "total_orders": sum(
                int(item["metrics"]["directly_reported"]["order_count"]) for item in ordered
            ),
            "worst_fold_return": canonical_decimal(min(returns)),
            "worst_maximum_drawdown": canonical_decimal(max(drawdowns)),
        },
    }


def aggregate_result_files(
    paths: Sequence[str | Path],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load and aggregate exactly five normalized result observations."""

    _require_exact_five_paths(paths)
    selected_bundle = bundle or load_protocol_bundle()
    return aggregate_result_observations(
        [load_result_observation(path, bundle=selected_bundle) for path in paths],
        bundle=selected_bundle,
    )


def write_result_aggregate_files(
    paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> Path:
    """Write a deterministic exact-five official-result aggregate."""

    _require_exact_five_paths(paths)
    destination = Path(output_path)
    if any(_path_identity(Path(path)) == _path_identity(destination) for path in paths):
        raise WalkForwardResultError("result aggregate output must differ from every input")
    aggregate = aggregate_result_files(paths, bundle=bundle)
    return _safe_atomic_json(destination, deterministic_json(aggregate))


def normalize_result_aggregate(
    payload: Mapping[str, object],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Recompute every supplied official-result aggregate field."""

    if not isinstance(payload, Mapping):
        raise WalkForwardResultError("result aggregate must be an object")
    _scan_normalized_output(payload, "result aggregate")
    folds = payload.get("fold_results")
    if isinstance(folds, (str, bytes)) or not isinstance(folds, Sequence):
        raise WalkForwardResultError("result aggregate fold_results must be an array")
    expected = aggregate_result_observations(folds, bundle=bundle)
    if dict(payload) != expected:
        raise WalkForwardResultError("result aggregate is not fully derived from its folds")
    return expected


def load_result_aggregate(
    path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load and fully recompute one normalized official-result aggregate."""

    selected = Path(path)
    _reject_path_links(selected, "result aggregate")
    _require_regular_file(selected, "result aggregate", _MAX_RESULT_AGGREGATE_BYTES)
    payload, raw = _load_bounded_json_object(
        selected, _MAX_RESULT_AGGREGATE_BYTES, "result aggregate"
    )
    normalized = normalize_result_aggregate(payload, bundle=bundle)
    if raw != deterministic_json(normalized).encode("utf-8"):
        raise WalkForwardResultError(
            "result aggregate must use sorted UTF-8 JSON with LF and a final newline"
        )
    return normalized


def _normalize_download_result(
    payload: Mapping[str, object], bundle: ProtocolBundle
) -> dict[str, Any]:
    state = _mapping(payload.get("state"), "result.state")
    configuration = _mapping(payload.get("algorithmConfiguration"), "result.algorithmConfiguration")
    parameters = _mapping(configuration.get("parameters"), "algorithmConfiguration.parameters")
    if set(parameters) != {"fold-id", "optimization-mode"}:
        raise WalkForwardResultError("result parameters must contain exactly the two fixed keys")
    fold_id = _nonempty_string(parameters.get("fold-id"), "parameters.fold-id")
    if fold_id not in FOLD_IDS:
        raise WalkForwardResultError("result contains an invalid fold-id")
    if parameters.get("optimization-mode") != "false":
        raise WalkForwardResultError("result optimization-mode must be exactly false")
    fold = fold_by_id(bundle, fold_id)
    expected_name = f"wf-v1-{fold_id}"

    if state.get("Status") != "Completed":
        raise WalkForwardResultError("result state status must be exactly Completed")
    if state.get("RuntimeError") != "":
        raise WalkForwardResultError("result contains a runtime error")
    if state.get("StackTrace") != "":
        raise WalkForwardResultError("result contains a stack trace")
    if state.get("Name") != expected_name:
        raise WalkForwardResultError("result state name differs from the exact fold name")
    if configuration.get("name") != expected_name:
        raise WalkForwardResultError(
            "algorithm configuration name differs from the exact fold name"
        )
    if configuration.get("accountCurrency") != "USD":
        raise WalkForwardResultError("algorithm account currency must be USD")
    if not _is_cash_account(configuration.get("accountType")):
        raise WalkForwardResultError("algorithm account type must be cash")
    if configuration.get("outOfSampleDays") != 0:
        raise WalkForwardResultError("out-of-sample days must be zero")
    _validate_optional_utc_metadata_timestamp(configuration.get("outOfSampleMaxEndDate"))
    if _parse_configuration_date(configuration.get("startDate"), "startDate") != date.fromisoformat(
        fold.evaluation_start
    ):
        raise WalkForwardResultError("algorithm configuration start date differs from its fold")
    if _parse_configuration_date(configuration.get("endDate"), "endDate") != date.fromisoformat(
        fold.evaluation_end
    ):
        raise WalkForwardResultError("algorithm configuration end date differs from its fold")

    reported, derived = _extract_metrics(payload, fold.evaluation_start, fold.evaluation_end)
    orders = _validate_orders_and_events(payload, fold.evaluation_start, fold.evaluation_end)
    reported.update(
        _extract_fee_evidence(
            payload,
            event_total_fees_usd=orders["event_total_fees_usd"],
        )
    )
    if orders["order_count"] != reported["order_count"]:
        raise WalkForwardResultError("reported order count disagrees with official orders")
    state_order_count = state.get("OrderCount")
    if state_order_count is not None:
        if _integer_text(state_order_count, "state.OrderCount") != orders["order_count"]:
            raise WalkForwardResultError("state order count disagrees with official orders")
    elif orders["order_validation_source"] == "completed_orders":
        raise WalkForwardResultError("state order count is required without order events")
    _validate_available_position_state(
        payload,
        orders,
        ending_equity=Decimal(str(reported["ending_equity_usd"])),
        total_return=Decimal(derived["total_return"]),
    )

    unavailable = list(_UNAVAILABLE_FIELDS)
    if orders["order_validation_source"] == "completed_orders":
        unavailable.append(_ORDER_EVENT_DETAIL_UNAVAILABLE)

    observation = {
        "configuration": {
            "account_currency": "USD",
            "account_type": "cash",
            "name": expected_name,
            "out_of_sample_days": 0,
            "parameters": {"fold-id": fold_id, "optimization-mode": "false"},
        },
        "evaluation_end": fold.evaluation_end,
        "evaluation_start": fold.evaluation_start,
        "fold_id": fold_id,
        "metrics": {
            "derived": derived,
            "directly_reported": reported,
            "unavailable": unavailable,
        },
        "orders": {
            "completed_order_count": orders["completed_order_count"],
            "final_position_quantity": canonical_decimal(orders["final_position_quantity"]),
            "final_position_state": ("cash" if orders["final_position_quantity"] == 0 else "long"),
            "order_validation_source": orders["order_validation_source"],
        },
        "protocol": {
            "content_sha256": bundle.manifest_sha256,
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
        },
        "record_type": RESULT_RECORD_TYPE,
        "schema_version": RESULT_SCHEMA_VERSION,
        "source": _source_identity(bundle),
        "source_format": RESULT_SOURCE_FORMAT,
        "state": {"completion_status": "completed"},
    }
    return normalize_result_observation(observation, bundle=bundle)


def _extract_metrics(
    payload: Mapping[str, object], evaluation_start: str, evaluation_end: str
) -> tuple[dict[str, object], dict[str, str]]:
    total_performance = _mapping(payload.get("totalPerformance"), "result.totalPerformance")
    portfolio = _mapping(
        total_performance.get("portfolioStatistics"),
        "totalPerformance.portfolioStatistics",
    )
    trades = _mapping(total_performance.get("tradeStatistics"), "totalPerformance.tradeStatistics")
    statistics = _mapping(payload.get("statistics"), "result.statistics")

    start_equity = _decimal(portfolio.get("startEquity"), "portfolio.startEquity")
    end_equity = _decimal(portfolio.get("endEquity"), "portfolio.endEquity")
    reported_return = _decimal(portfolio.get("totalNetProfit"), "portfolio.totalNetProfit")
    drawdown = _decimal(portfolio.get("drawdown"), "portfolio.drawdown")
    sharpe = _decimal(portfolio.get("sharpeRatio"), "portfolio.sharpeRatio")
    sortino = _decimal(portfolio.get("sortinoRatio"), "portfolio.sortinoRatio")
    probabilistic = _decimal(
        portfolio.get("probabilisticSharpeRatio"),
        "portfolio.probabilisticSharpeRatio",
    )
    trade_analysis_fees = _decimal(trades.get("totalFees"), "tradeStatistics.totalFees")
    if start_equity != Decimal("100000"):
        raise WalkForwardResultError("result starting equity must be exactly 100000")
    if end_equity <= 0:
        raise WalkForwardResultError("result ending equity must be positive")
    if not Decimal("0") <= drawdown <= Decimal("1"):
        raise WalkForwardResultError("result maximum drawdown must be within zero and one")
    if not Decimal("0") <= probabilistic <= Decimal("1"):
        raise WalkForwardResultError(
            "result probabilistic Sharpe ratio must be within zero and one"
        )
    if trade_analysis_fees < 0:
        raise WalkForwardResultError("tradeStatistics.totalFees must be non-negative")
    derived_return = end_equity / start_equity - Decimal("1")
    if abs(reported_return - derived_return) > _ROUNDED_RATIO_TOLERANCE:
        raise WalkForwardResultError("result return and equity values are contradictory")

    order_count = _integer_text(statistics.get("Total Orders"), "statistics.Total Orders")
    if order_count < 0:
        raise WalkForwardResultError("result order count must be non-negative")
    _require_dashboard_close(
        statistics.get("Start Equity"), start_equity, "statistics.Start Equity", currency=True
    )
    _require_dashboard_close(
        statistics.get("End Equity"), end_equity, "statistics.End Equity", currency=True
    )
    _require_dashboard_close(
        statistics.get("Net Profit"), derived_return, "statistics.Net Profit", percent=True
    )
    _require_dashboard_close(
        statistics.get("Drawdown"), drawdown, "statistics.Drawdown", percent=True
    )
    _require_dashboard_close(statistics.get("Sharpe Ratio"), sharpe, "statistics.Sharpe Ratio")
    _require_dashboard_close(statistics.get("Sortino Ratio"), sortino, "statistics.Sortino Ratio")
    _require_dashboard_close(
        statistics.get("Probabilistic Sharpe Ratio"),
        probabilistic,
        "statistics.Probabilistic Sharpe Ratio",
        percent=True,
    )

    benchmark_start, benchmark_end = _extract_benchmark(
        payload.get("charts"), evaluation_start, evaluation_end
    )
    benchmark_return = benchmark_end / benchmark_start - Decimal("1")
    reported = {
        "ending_equity_usd": canonical_decimal(end_equity),
        "maximum_drawdown": canonical_decimal(drawdown),
        "order_count": order_count,
        "probabilistic_sharpe_ratio": canonical_decimal(probabilistic),
        "sharpe_ratio": canonical_decimal(sharpe),
        "sortino_ratio": canonical_decimal(sortino),
        "starting_equity_usd": "100000",
    }
    derived = {
        "benchmark_ending_value": canonical_decimal(benchmark_end),
        "benchmark_return": canonical_decimal(benchmark_return),
        "benchmark_starting_value": canonical_decimal(benchmark_start),
        "excess_return": canonical_decimal(derived_return - benchmark_return),
        "total_return": canonical_decimal(derived_return),
    }
    return reported, derived


def _extract_fee_evidence(
    payload: Mapping[str, object], *, event_total_fees_usd: Decimal | None
) -> dict[str, object]:
    statistics = _mapping(payload.get("statistics"), "result.statistics")
    overview_fees = _dashboard_decimal(statistics.get("Total Fees"), "statistics.Total Fees")
    if overview_fees < 0:
        raise WalkForwardResultError("statistics.Total Fees must be non-negative")

    runtime = _mapping(payload.get("runtimeStatistics"), "result.runtimeStatistics")
    runtime_fees = _dashboard_decimal(runtime.get("Fees"), "runtimeStatistics.Fees").copy_abs()
    _require_cent_precision(overview_fees, "statistics.Total Fees")
    _require_cent_precision(runtime_fees, "runtimeStatistics.Fees")

    if event_total_fees_usd is not None:
        if _decimal_difference_exceeds(
            overview_fees, event_total_fees_usd, _ROUNDED_CURRENCY_TOLERANCE
        ):
            raise WalkForwardResultError(
                "statistics.Total Fees contradicts authoritative order-event fees"
            )
        if _decimal_difference_exceeds(
            runtime_fees, event_total_fees_usd, _ROUNDED_CURRENCY_TOLERANCE
        ):
            raise WalkForwardResultError(
                "runtimeStatistics.Fees contradicts authoritative order-event fees"
            )
        return {
            "fee_precision": "order_event_amount_precision",
            "fee_validation_source": "order_events",
            "order_event_fee_evidence_available": True,
            "total_fees_usd": canonical_decimal(event_total_fees_usd),
        }

    if _decimal_difference_exceeds(overview_fees, runtime_fees, _ROUNDED_CURRENCY_TOLERANCE):
        raise WalkForwardResultError("statistics.Total Fees and runtimeStatistics.Fees disagree")
    return {
        "fee_precision": "rounded_to_cent",
        "fee_validation_source": "overview_runtime_rounded",
        "order_event_fee_evidence_available": False,
        "total_fees_usd": canonical_decimal(overview_fees),
    }


def _extract_benchmark(
    charts_value: object, evaluation_start: str, evaluation_end: str
) -> tuple[Decimal, Decimal]:
    charts = _mapping(charts_value, "result.charts")
    benchmark = _mapping(charts.get("Benchmark"), "charts.Benchmark")
    series = _mapping(benchmark.get("series"), "charts.Benchmark.series")
    if set(series) != {"Benchmark"}:
        raise WalkForwardResultError("Benchmark chart must contain one unambiguous series")
    selected = _mapping(series["Benchmark"], "charts.Benchmark.series.Benchmark")
    values = selected.get("values")
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise WalkForwardResultError("Benchmark series values must be an array")
    if len(values) < 2 or len(values) > _MAX_CHART_POINTS:
        raise WalkForwardResultError("Benchmark chart has insufficient or excessive points")
    start = date.fromisoformat(evaluation_start)
    end = date.fromisoformat(evaluation_end)
    points: list[tuple[datetime, Decimal]] = []
    prior: datetime | None = None
    for index, value in enumerate(values):
        if isinstance(value, Mapping):
            if set(value) != {"x", "y"}:
                raise WalkForwardResultError("Benchmark point object must contain exactly x and y")
            raw_timestamp = value["x"]
            raw_amount = value["y"]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if len(value) != 2:
                raise WalkForwardResultError(
                    "Benchmark point array must contain exactly two values"
                )
            raw_timestamp, raw_amount = value
        else:
            raise WalkForwardResultError("Benchmark point has an unsupported shape")
        timestamp = _chart_timestamp(raw_timestamp, f"Benchmark.values[{index}].timestamp")
        amount = _numeric_decimal(raw_amount, f"Benchmark.values[{index}].value")
        if amount <= 0:
            raise WalkForwardResultError("Benchmark values must be positive")
        if prior is not None and timestamp <= prior:
            raise WalkForwardResultError("Benchmark timestamps must be strictly increasing")
        prior = timestamp
        if start <= timestamp.date() <= end:
            points.append((timestamp, amount))
    if len(points) < 2:
        raise WalkForwardResultError("Benchmark chart does not cover the declared fold")
    if points[0][0].date() > start + _BENCHMARK_BOUNDARY_TOLERANCE:
        raise WalkForwardResultError("Benchmark chart starts too late for the declared fold")
    if points[-1][0].date() < end - _BENCHMARK_BOUNDARY_TOLERANCE:
        raise WalkForwardResultError("Benchmark chart ends too early for the declared fold")
    return points[0][1], points[-1][1]


def _validate_orders_and_events(
    payload: Mapping[str, object], evaluation_start: str, evaluation_end: str
) -> dict[str, Any]:
    raw_orders = payload.get("orders")
    if isinstance(raw_orders, Mapping):
        order_values = list(raw_orders.values())
    elif isinstance(raw_orders, Sequence) and not isinstance(raw_orders, (str, bytes)):
        order_values = list(raw_orders)
    else:
        raise WalkForwardResultError("result orders must be an object or array")
    if len(order_values) > _MAX_ORDERS:
        raise WalkForwardResultError("result contains too many orders")
    start = date.fromisoformat(evaluation_start)
    end = date.fromisoformat(evaluation_end)
    orders: dict[int, dict[str, Any]] = {}
    events_present = "orderEvents" in payload
    for index, raw in enumerate(order_values):
        order = _mapping(raw, f"orders[{index}]")
        order_id = _integer(order.get("id"), f"orders[{index}].id")
        if order_id in orders:
            raise WalkForwardResultError("result contains a duplicate order")
        _require_spy_symbol(order.get("symbol"), f"orders[{index}].symbol")
        price = _decimal(order.get("price"), f"orders[{index}].price")
        quantity = _decimal(order.get("quantity"), f"orders[{index}].quantity")
        value = _decimal(order.get("value"), f"orders[{index}].value")
        timestamp = _utc_timestamp(order.get("time"), f"orders[{index}].time")
        status = _order_status(order.get("status"), f"orders[{index}].status")
        if order.get("priceCurrency") != "USD":
            raise WalkForwardResultError("order price currency must be USD")
        if price < 0 or quantity == 0:
            raise WalkForwardResultError("order price and quantity are invalid")
        if not start <= timestamp.date() <= end:
            raise WalkForwardResultError("order timestamp falls outside the declared fold")
        if abs(value - quantity * price) > _ROUNDED_CURRENCY_TOLERANCE:
            raise WalkForwardResultError("order value disagrees with quantity and price")
        orders[order_id] = {
            "price": price,
            "quantity": quantity,
            "status": status,
            "time": timestamp,
            "value": value,
        }

    if not events_present:
        return _validate_completed_orders_without_events(orders, order_values, start, end)

    events_value = payload["orderEvents"]
    if isinstance(events_value, (str, bytes)) or not isinstance(events_value, Sequence):
        raise WalkForwardResultError("result orderEvents must be an array")
    if len(events_value) > _MAX_ORDERS * 10:
        raise WalkForwardResultError("result contains too many order events")
    fills: dict[int, Decimal] = {order_id: Decimal("0") for order_id in orders}
    fee_amounts: list[Decimal] = []
    for index, raw in enumerate(events_value):
        event = _mapping(raw, f"orderEvents[{index}]")
        order_id = _integer(event.get("orderId"), f"orderEvents[{index}].orderId")
        if order_id not in orders:
            raise WalkForwardResultError("order event references an unknown order")
        _require_spy_symbol(event.get("symbol"), f"orderEvents[{index}].symbol")
        timestamp = _utc_timestamp(event.get("utcTime"), f"orderEvents[{index}].utcTime")
        if not start <= timestamp.date() <= end:
            raise WalkForwardResultError("order event timestamp falls outside the declared fold")
        fill_price = _decimal(event.get("fillPrice"), f"orderEvents[{index}].fillPrice")
        fill_quantity = _decimal(event.get("fillQuantity"), f"orderEvents[{index}].fillQuantity")
        status = _order_status(event.get("status"), f"orderEvents[{index}].status")
        if event.get("fillPriceCurrency") != "USD":
            raise WalkForwardResultError("order event fill currency must be USD")
        if fill_price < 0:
            raise WalkForwardResultError("order event fill price must be non-negative")
        fee = _mapping(event.get("orderFee"), f"orderEvents[{index}].orderFee")
        fee_value = _mapping(fee.get("value"), f"orderEvents[{index}].orderFee.value")
        amount = _decimal(fee_value.get("amount"), f"orderEvents[{index}].fee.amount")
        currency = fee_value.get("currency")
        if amount < 0 or currency not in {"USD", ""} or (amount != 0 and currency != "USD"):
            raise WalkForwardResultError("order event fee must be non-negative USD")
        fee_amounts.append(amount)
        if status in {2, 3}:
            if fill_quantity == 0 or fill_price <= 0:
                raise WalkForwardResultError("filled order events require price and quantity")
            fills[order_id] += fill_quantity
        elif fill_quantity != 0:
            raise WalkForwardResultError("unfilled order event has a non-zero fill quantity")

    completed = 0
    ordered_fills: list[tuple[datetime, int, Decimal]] = []
    for order_id, order in orders.items():
        if order["status"] == 3:
            completed += 1
            if fills[order_id] != order["quantity"]:
                raise WalkForwardResultError("filled order quantity disagrees with order events")
            ordered_fills.append((order["time"], order_id, fills[order_id]))
        elif fills[order_id] != 0:
            raise WalkForwardResultError("non-filled order contains completed fill quantity")
    position = Decimal("0")
    for _timestamp, _order_id, quantity in sorted(ordered_fills):
        position += quantity
        if position < 0:
            raise WalkForwardResultError("completed orders create an unsupported short position")
    return {
        "completed_order_count": completed,
        "event_total_fees_usd": _exact_decimal_sum(fee_amounts),
        "final_position_quantity": position,
        "order_count": len(orders),
        "order_validation_source": "order_events",
    }


def _validate_completed_orders_without_events(
    orders: Mapping[int, Mapping[str, Any]],
    order_values: Sequence[object],
    start: date,
    end: date,
) -> dict[str, Any]:
    ordered_fills: list[tuple[datetime, int, Decimal]] = []
    for index, (order_id, parsed_order) in enumerate(orders.items()):
        raw_order = _mapping(order_values[index], f"orders[{index}]")
        if parsed_order["status"] != 3:
            raise WalkForwardResultError("orders without order events must all be filled")
        if parsed_order["price"] <= 0:
            raise WalkForwardResultError("filled orders require a positive fill price")
        last_fill_time = _utc_timestamp(
            raw_order.get("lastFillTime"), f"orders[{index}].lastFillTime"
        )
        if not start <= last_fill_time.date() <= end:
            raise WalkForwardResultError("order fill timestamp falls outside the declared fold")
        ordered_fills.append((last_fill_time, order_id, parsed_order["quantity"]))

    position = Decimal("0")
    for _timestamp, _order_id, quantity in sorted(ordered_fills):
        position += quantity
        if position < 0:
            raise WalkForwardResultError("completed orders create an unsupported short position")
    return {
        "completed_order_count": len(orders),
        "event_total_fees_usd": None,
        "final_position_quantity": position,
        "order_count": len(orders),
        "order_validation_source": "completed_orders",
    }


def _validate_available_position_state(
    payload: Mapping[str, object],
    orders: Mapping[str, Any],
    *,
    ending_equity: Decimal,
    total_return: Decimal,
) -> None:
    runtime = _mapping(payload.get("runtimeStatistics"), "result.runtimeStatistics")
    holdings = _dashboard_decimal(runtime.get("Holdings"), "runtimeStatistics.Holdings")
    equity = _dashboard_decimal(runtime.get("Equity"), "runtimeStatistics.Equity")
    runtime_return = _dashboard_decimal(
        runtime.get("Return"), "runtimeStatistics.Return", percent=True
    )
    if holdings < 0 or equity <= 0:
        raise WalkForwardResultError("runtime result state contains invalid financial values")
    if abs(equity - ending_equity) > _ROUNDED_CURRENCY_TOLERANCE:
        raise WalkForwardResultError("runtime equity disagrees with official performance")
    if abs(runtime_return - total_return) > _ROUNDED_RATIO_TOLERANCE:
        raise WalkForwardResultError("runtime return disagrees with official performance")
    position = orders["final_position_quantity"]
    if (position == 0) != (holdings == 0):
        raise WalkForwardResultError("final order position disagrees with available result state")


def _normalize_output_metrics(value: object, *, order_validation_source: str) -> dict[str, Any]:
    metrics = _mapping(value, "result observation.metrics")
    _require_exact_fields(
        metrics, {"derived", "directly_reported", "unavailable"}, "result observation.metrics"
    )
    reported = _mapping(metrics["directly_reported"], "metrics.directly_reported")
    _require_exact_fields(
        reported,
        {
            "ending_equity_usd",
            "fee_precision",
            "fee_validation_source",
            "maximum_drawdown",
            "order_event_fee_evidence_available",
            "order_count",
            "probabilistic_sharpe_ratio",
            "sharpe_ratio",
            "sortino_ratio",
            "starting_equity_usd",
            "total_fees_usd",
        },
        "metrics.directly_reported",
    )
    normalized_reported: dict[str, object] = {}
    decimal_fields = {
        "ending_equity_usd",
        "maximum_drawdown",
        "probabilistic_sharpe_ratio",
        "sharpe_ratio",
        "sortino_ratio",
        "starting_equity_usd",
        "total_fees_usd",
    }
    for field in sorted(decimal_fields):
        normalized_reported[field] = _canonical_decimal_string(
            reported[field], f"metrics.directly_reported.{field}"
        )
    order_count = reported["order_count"]
    if isinstance(order_count, bool) or not isinstance(order_count, int) or order_count < 0:
        raise WalkForwardResultError("reported order count must be a non-negative integer")
    normalized_reported["order_count"] = order_count

    fee_validation_source = reported["fee_validation_source"]
    if (
        not isinstance(fee_validation_source, str)
        or fee_validation_source not in _FEE_VALIDATION_SOURCES
    ):
        raise WalkForwardResultError("normalized fee validation source is unsupported")
    fee_precision = reported["fee_precision"]
    if not isinstance(fee_precision, str) or fee_precision not in _FEE_PRECISIONS:
        raise WalkForwardResultError("normalized fee precision is unsupported")
    event_fee_evidence = reported["order_event_fee_evidence_available"]
    if not isinstance(event_fee_evidence, bool):
        raise WalkForwardResultError(
            "normalized order-event fee evidence availability must be boolean"
        )

    expected_fee_evidence: dict[str, object]
    if order_validation_source == "order_events":
        expected_fee_evidence = {
            "fee_precision": "order_event_amount_precision",
            "fee_validation_source": "order_events",
            "order_event_fee_evidence_available": True,
        }
    else:
        expected_fee_evidence = {
            "fee_precision": "rounded_to_cent",
            "fee_validation_source": "overview_runtime_rounded",
            "order_event_fee_evidence_available": False,
        }
    if {
        "fee_precision": fee_precision,
        "fee_validation_source": fee_validation_source,
        "order_event_fee_evidence_available": event_fee_evidence,
    } != expected_fee_evidence:
        raise WalkForwardResultError(
            "normalized fee evidence metadata disagrees with order evidence"
        )
    normalized_reported.update(expected_fee_evidence)

    if normalized_reported["starting_equity_usd"] != "100000":
        raise WalkForwardResultError("normalized result starting equity differs from v1")
    if Decimal(str(normalized_reported["ending_equity_usd"])) <= 0:
        raise WalkForwardResultError("normalized result ending equity must be positive")
    normalized_total_fees = Decimal(str(normalized_reported["total_fees_usd"]))
    if normalized_total_fees < 0:
        raise WalkForwardResultError("normalized result total fees must be non-negative")
    if fee_precision == "rounded_to_cent":
        _require_cent_precision(normalized_total_fees, "normalized result total fees")
    drawdown = Decimal(str(normalized_reported["maximum_drawdown"]))
    probabilistic = Decimal(str(normalized_reported["probabilistic_sharpe_ratio"]))
    if not Decimal("0") <= drawdown <= Decimal("1"):
        raise WalkForwardResultError("normalized result drawdown is invalid")
    if not Decimal("0") <= probabilistic <= Decimal("1"):
        raise WalkForwardResultError("normalized probabilistic Sharpe ratio is invalid")

    derived = _mapping(metrics["derived"], "metrics.derived")
    _require_exact_fields(
        derived,
        {
            "benchmark_ending_value",
            "benchmark_return",
            "benchmark_starting_value",
            "excess_return",
            "total_return",
        },
        "metrics.derived",
    )
    normalized_derived = {
        field: _canonical_decimal_string(value, f"metrics.derived.{field}")
        for field, value in sorted(derived.items())
    }
    start = Decimal(str(normalized_reported["starting_equity_usd"]))
    end = Decimal(str(normalized_reported["ending_equity_usd"]))
    benchmark_start = Decimal(normalized_derived["benchmark_starting_value"])
    benchmark_end = Decimal(normalized_derived["benchmark_ending_value"])
    if benchmark_start <= 0 or benchmark_end <= 0:
        raise WalkForwardResultError("normalized benchmark values must be positive")
    expected_return = canonical_decimal(end / start - Decimal("1"))
    expected_benchmark = canonical_decimal(benchmark_end / benchmark_start - Decimal("1"))
    expected_excess = canonical_decimal(Decimal(expected_return) - Decimal(expected_benchmark))
    if normalized_derived != {
        "benchmark_ending_value": canonical_decimal(benchmark_end),
        "benchmark_return": expected_benchmark,
        "benchmark_starting_value": canonical_decimal(benchmark_start),
        "excess_return": expected_excess,
        "total_return": expected_return,
    }:
        raise WalkForwardResultError("normalized result derived metrics are inconsistent")
    unavailable = metrics["unavailable"]
    expected_unavailable = list(_UNAVAILABLE_FIELDS)
    if order_validation_source == "completed_orders":
        expected_unavailable.append(_ORDER_EVENT_DETAIL_UNAVAILABLE)
    if (
        not isinstance(unavailable, Sequence)
        or isinstance(unavailable, (str, bytes))
        or list(unavailable) != expected_unavailable
    ):
        raise WalkForwardResultError("normalized unavailable fields differ from the fixed contract")
    return {
        "derived": normalized_derived,
        "directly_reported": dict(sorted(normalized_reported.items())),
        "unavailable": expected_unavailable,
    }


def _normalize_output_orders(value: object) -> dict[str, Any]:
    orders = _mapping(value, "result observation.orders")
    _require_exact_fields(
        orders,
        {
            "completed_order_count",
            "final_position_quantity",
            "final_position_state",
            "order_validation_source",
        },
        "result observation.orders",
    )
    count = orders["completed_order_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise WalkForwardResultError("completed order count must be a non-negative integer")
    quantity = _canonical_decimal_string(
        orders["final_position_quantity"], "orders.final_position_quantity"
    )
    if Decimal(quantity) < 0:
        raise WalkForwardResultError("normalized final position cannot be short")
    state = orders["final_position_state"]
    if state not in {"cash", "long"} or ((state == "cash") != (Decimal(quantity) == 0)):
        raise WalkForwardResultError("normalized final position state is inconsistent")
    source = orders["order_validation_source"]
    if source not in _ORDER_VALIDATION_SOURCES:
        raise WalkForwardResultError("normalized order validation source is unsupported")
    return {
        "completed_order_count": count,
        "final_position_quantity": quantity,
        "final_position_state": state,
        "order_validation_source": source,
    }


def _source_identity(bundle: ProtocolBundle) -> dict[str, str]:
    return {
        "importer_schema_sha256": _schema_sha256(
            RESULT_OBSERVATION_SCHEMA_PATH, "result observation schema"
        ),
        "importer_schema_version": RESULT_SCHEMA_VERSION,
        "project_source_sha256": bundle.project_source_sha256,
        "public_configuration_sha256": bundle.public_configuration_sha256,
        "source_verification_attestation": SOURCE_VERIFICATION_ATTESTATION,
    }


def _schema_sha256(path: Path, label: str) -> str:
    try:
        payload, raw = load_json_object(path)
        canonical = deterministic_json(payload).encode("utf-8")
    except (OSError, TypeError, ValueError) as exc:
        raise WalkForwardResultError(f"{label} could not be validated") from exc
    if raw != canonical:
        raise WalkForwardResultError(f"{label} must use deterministic UTF-8 JSON")
    return sha256(raw).hexdigest()


def _scan_input_for_stale_project(value: object) -> None:
    pending = [value]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        if inspected > _MAX_UNTRUSTED_NODES:
            raise WalkForwardResultError("input result exceeds the fixed structural limit")
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend(current)
        elif isinstance(current, str) and _STALE_PROJECT.search(current):
            raise WalkForwardResultError("result identifies the excluded stale duplicate project")


def _scan_normalized_output(value: object, label: str) -> None:
    pending = [value]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        if inspected > _MAX_UNTRUSTED_NODES:
            raise WalkForwardResultError(f"{label} exceeds the fixed structural limit")
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                if not isinstance(raw_key, str):
                    raise WalkForwardResultError(f"{label} contains a non-string field name")
                if _normalize_key(raw_key) in _OUTPUT_PRIVATE_KEYS:
                    raise WalkForwardResultError(f"{label} contains a private identity field")
                pending.append(child)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend(current)
        elif isinstance(current, str):
            if any(ord(character) < 32 for character in current):
                raise WalkForwardResultError(f"{label} contains control characters")
            if _EMAIL.search(current) or _SECRET.search(current) or "://" in current:
                raise WalkForwardResultError(f"{label} contains private or credential text")
            if current.startswith("-----BEGIN ") or current.casefold().startswith("bearer "):
                raise WalkForwardResultError(f"{label} contains authentication material")


def _load_bounded_json_object(
    path: Path, max_bytes: int, label: str
) -> tuple[dict[str, Any], bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise WalkForwardResultError(f"{label} could not be loaded") from exc
    if len(raw) > max_bytes:
        raise WalkForwardResultError(f"{label} exceeds the fixed byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WalkForwardResultError(f"{label} must be valid UTF-8") from exc

    def reject_constant(_value: str) -> None:
        raise WalkForwardResultError(f"{label} contains a non-finite JSON value")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WalkForwardResultError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=Decimal,
        )
    except WalkForwardResultError:
        raise
    except (
        json.JSONDecodeError,
        DecimalException,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        raise WalkForwardResultError(f"{label} contains invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WalkForwardResultError(f"{label} must contain a JSON object")
    return payload, raw


def _safe_atomic_json(destination: Path, serialized: str) -> Path:
    if not destination.name or destination.suffix.lower() != ".json":
        raise WalkForwardResultError("output path must name a JSON file")
    _reject_path_links(destination, "output destination")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WalkForwardResultError("output directory could not be prepared") from exc
    _reject_path_links(destination, "output destination")
    try:
        return atomic_write_text(destination, serialized)
    except OSError as exc:
        raise WalkForwardResultError("output JSON could not be written") from exc


def _reject_path_links(path: Path, label: str) -> None:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WalkForwardResultError(f"{label} could not be inspected safely") from exc
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        is_reparse = bool(os.name == "nt" and reparse_flag and attributes & reparse_flag)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise WalkForwardResultError(f"{label} must not traverse symlinks or reparse points")


def _require_regular_file(path: Path, label: str, max_bytes: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WalkForwardResultError(f"{label} could not be inspected safely") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise WalkForwardResultError(f"{label} must be a regular file")
    if metadata.st_size > max_bytes:
        raise WalkForwardResultError(f"{label} exceeds the fixed byte limit")


def _path_identity(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WalkForwardResultError("artifact path could not be resolved safely") from exc


def _require_exact_five_paths(paths: Sequence[str | Path]) -> None:
    if (
        isinstance(paths, (str, bytes))
        or not isinstance(paths, Sequence)
        or len(paths) != len(FOLD_IDS)
    ):
        raise WalkForwardResultError("result aggregation requires exactly five artifact paths")


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WalkForwardResultError(f"{path} must be an object")
    return value


def _require_exact_fields(value: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise WalkForwardResultError(f"{path} fields differ from the fixed result contract")


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise WalkForwardResultError(f"{path} must be a non-empty string")
    return value


def _decimal(value: object, path: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise WalkForwardResultError(f"{path} must be a finite decimal")
    if isinstance(value, str) and len(value) > _MAX_DECIMAL_TEXT_CHARACTERS:
        raise WalkForwardResultError(f"{path} exceeds the fixed decimal bound")
    try:
        selected = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError) as exc:
        raise WalkForwardResultError(f"{path} must be a finite decimal") from exc
    if not selected.is_finite() or abs(selected.adjusted()) > _MAX_DECIMAL_ADJUSTED_EXPONENT:
        raise WalkForwardResultError(f"{path} must be a bounded finite decimal")
    return selected


def _numeric_decimal(value: object, path: str) -> Decimal:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        raise WalkForwardResultError(f"{path} must be a finite JSON number")
    return _decimal(value, path)


def _canonical_decimal_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise WalkForwardResultError(f"{path} must be a canonical decimal string")
    try:
        selected = _decimal(value, path)
        canonical = canonical_decimal(selected)
    except ValueError as exc:
        raise WalkForwardResultError(f"{path} must be a canonical decimal string") from exc
    if value != canonical:
        raise WalkForwardResultError(f"{path} must be a canonical decimal string")
    return value


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WalkForwardResultError(f"{path} must be an integer")
    return value


def _integer_text(value: object, path: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise WalkForwardResultError(f"{path} must be a non-negative integer")
    return int(value)


def _is_cash_account(value: object) -> bool:
    return (isinstance(value, str) and value.casefold() == "cash") or value == 1


def _parse_configuration_date(value: object, path: str) -> date:
    selected = _nonempty_string(value, f"algorithmConfiguration.{path}")
    if len(selected) > 64:
        raise WalkForwardResultError(f"algorithmConfiguration.{path} is not an ISO date")
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", selected):
            return date.fromisoformat(selected)
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WalkForwardResultError(f"algorithmConfiguration.{path} is not an ISO date") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise WalkForwardResultError(f"algorithmConfiguration.{path} must be UTC")
    return parsed.astimezone(UTC).date()


def _validate_optional_utc_metadata_timestamp(value: object) -> None:
    if value in {None, ""}:
        return
    selected = _nonempty_string(value, "algorithmConfiguration.outOfSampleMaxEndDate")
    if len(selected) > 64:
        raise WalkForwardResultError("out-of-sample metadata timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WalkForwardResultError("out-of-sample metadata timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise WalkForwardResultError("out-of-sample metadata timestamp must be UTC")
    normalized = parsed.astimezone(UTC)
    if not datetime.min.replace(tzinfo=UTC) <= normalized <= datetime.max.replace(tzinfo=UTC):
        raise WalkForwardResultError("out-of-sample metadata timestamp is out of bounds")


def _utc_timestamp(value: object, path: str) -> datetime:
    selected = _nonempty_string(value, path)
    try:
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WalkForwardResultError(f"{path} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _chart_timestamp(value: object, path: str) -> datetime:
    if isinstance(value, bool):
        raise WalkForwardResultError(f"{path} must be a Unix timestamp")
    if isinstance(value, (int, Decimal)):
        selected = _decimal(value, path)
        if (
            selected != selected.to_integral_value()
            or selected < 0
            or selected > Decimal("4102444800")
        ):
            raise WalkForwardResultError(f"{path} must be bounded Unix seconds")
        try:
            return datetime.fromtimestamp(int(selected), tz=UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise WalkForwardResultError(f"{path} must be bounded Unix seconds") from exc
    raise WalkForwardResultError(f"{path} must be bounded Unix seconds")


def _order_status(value: object, path: str) -> int:
    names = {"partiallyfilled": 2, "filled": 3, "canceled": 5, "cancelled": 5, "invalid": 7}
    if isinstance(value, str):
        normalized = re.sub(r"[^a-z]", "", value.casefold())
        if normalized in names:
            return names[normalized]
    if isinstance(value, int) and not isinstance(value, bool) and value in {2, 3, 5, 7}:
        return value
    raise WalkForwardResultError(f"{path} is not a supported completed order status")


def _require_spy_symbol(value: object, path: str) -> None:
    if isinstance(value, str):
        symbol = value.split()[0]
    elif isinstance(value, Mapping):
        symbol = value.get("value")
    else:
        raise WalkForwardResultError(f"{path} must identify SPY")
    if symbol != "SPY":
        raise WalkForwardResultError("all result orders and events must belong only to SPY")


def _dashboard_decimal(value: object, path: str, *, percent: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > _MAX_DECIMAL_TEXT_CHARACTERS:
        raise WalkForwardResultError(f"{path} must be a bounded display decimal")
    selected = value.strip().replace(",", "").replace("$", "")
    observed_percent = selected.endswith("%")
    if observed_percent:
        selected = selected[:-1]
    if observed_percent and not percent:
        raise WalkForwardResultError(f"{path} must not use percent units")
    parsed = _decimal(selected, path)
    if percent:
        parsed /= Decimal("100")
    return parsed


def _require_cent_precision(value: Decimal, path: str) -> None:
    decimal_tuple = value.as_tuple()
    exponent = decimal_tuple.exponent
    if not isinstance(exponent, int):
        raise WalkForwardResultError(f"{path} must be a finite cent-precision value")
    digits_beyond_cents = max(0, -exponent - 2)
    if digits_beyond_cents and any(decimal_tuple.digits[-digits_beyond_cents:]):
        raise WalkForwardResultError(f"{path} must be rounded to cent precision")


def _require_dashboard_close(
    value: object,
    authoritative: Decimal,
    path: str,
    *,
    currency: bool = False,
    percent: bool = False,
) -> None:
    observed = _dashboard_decimal(value, path, percent=percent)
    tolerance = _ROUNDED_CURRENCY_TOLERANCE if currency else _ROUNDED_RATIO_TOLERANCE
    if abs(observed - authoritative) > tolerance:
        raise WalkForwardResultError(f"{path} contradicts the authoritative result value")


def _normalize_key(value: str) -> str:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", separated.casefold()).strip("_")


def _reported_metric(observation: Mapping[str, Any], field: str) -> Decimal:
    return Decimal(str(observation["metrics"]["directly_reported"][field]))


def _derived_metric(observation: Mapping[str, Any], field: str) -> Decimal:
    return Decimal(str(observation["metrics"]["derived"][field]))


def _exact_decimal_sum(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    exponents = [value.as_tuple().exponent for value in values]
    if any(not isinstance(exponent, int) for exponent in exponents):
        raise WalkForwardResultError("fee values must be finite decimals")
    common_exponent = min(exponents)
    total_coefficient = 0
    for value in values:
        sign, digits, exponent = value.as_tuple()
        coefficient = int("".join(str(digit) for digit in digits))
        if sign:
            coefficient = -coefficient
        total_coefficient += coefficient * 10 ** (int(exponent) - common_exponent)
    sign = int(total_coefficient < 0)
    absolute = str(abs(total_coefficient))
    return Decimal((sign, tuple(int(digit) for digit in absolute), common_exponent))


def _decimal_difference_exceeds(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    difference = _exact_decimal_sum((left, right.copy_negate())).copy_abs()
    return difference > tolerance


def _median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise WalkForwardResultError("cannot summarize an empty result metric sequence")
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


__all__ = [
    "RESULT_AGGREGATE_RECORD_TYPE",
    "RESULT_AGGREGATE_SCHEMA_PATH",
    "RESULT_OBSERVATION_SCHEMA_PATH",
    "RESULT_RECORD_TYPE",
    "RESULT_SCHEMA_VERSION",
    "RESULT_SOURCE_FORMAT",
    "SOURCE_VERIFICATION_ATTESTATION",
    "WalkForwardResultError",
    "aggregate_result_files",
    "aggregate_result_observations",
    "extract_result_json",
    "load_result_aggregate",
    "load_result_observation",
    "normalize_result_aggregate",
    "normalize_result_observation",
    "parse_result_json",
    "write_result_aggregate_files",
]
