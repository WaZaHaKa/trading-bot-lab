"""Strict offline extraction and aggregation for walk-forward protocol v1."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException
from math import isfinite
from pathlib import Path
from typing import Any, BinaryIO

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward.contract import (
    ENGINE_NAME,
    ENGINE_VERSION_PATTERN,
    FOLD_IDS,
    MAX_OBSERVATION_PAYLOAD_BYTES,
    OBSERVATION_PREFIX,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    ProtocolBundle,
    WalkForwardContractError,
    compact_json,
    fold_by_id,
    load_protocol_bundle,
)

_PREFIX_BYTES = OBSERVATION_PREFIX.encode("ascii")
_MAX_LOG_LINE_BYTES = len(_PREFIX_BYTES) + MAX_OBSERVATION_PAYLOAD_BYTES + 2
_MAX_INPUT_LOG_BYTES = 8 * 1024 * 1024
_MAX_NORMALIZED_OBSERVATION_BYTES = 64 * 1024
_MAX_AGGREGATE_RECORD_BYTES = 512 * 1024
_ENGINE_VERSION = re.compile(ENGINE_VERSION_PATTERN)
_MAX_DECIMAL_TEXT_CHARACTERS = 128
_MAX_DECIMAL_ADJUSTED_EXPONENT = 100
_MAX_UNTRUSTED_NODES = 10_000
_CANONICAL_DECIMAL_TEXT = re.compile(r"^(?:0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$")
_EMAIL = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:"
    r"gh[pousr]_[A-Za-z0-9]{10,}|"
    r"sk-[A-Za-z0-9_-]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}"
    r")"
)
_IDENTITY_TEXT = re.compile(
    r"(?i)\b(?:account|backtest|billing|cloud|credential|email|invitation|invoice|"
    r"license|organization|owner|password|project|secret|subscription|token|url)"
    r"[ _-]?(?:id|key|name|number|path|secret|token|url)?\b"
)
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "accountemail",
        "accountid",
        "accountname",
        "accountnumber",
        "apikey",
        "backtestid",
        "billing",
        "cloudid",
        "credential",
        "credentials",
        "email",
        "invitation",
        "invoice",
        "license",
        "localid",
        "modulelicense",
        "nodeid",
        "organizationid",
        "owner",
        "password",
        "path",
        "paths",
        "privatekey",
        "projectid",
        "secret",
        "subscription",
        "token",
        "url",
    }
)

_TOP_FIELDS = {
    "costs",
    "data",
    "engine",
    "evaluation_end",
    "evaluation_start",
    "execution",
    "fold_id",
    "metrics",
    "protocol_version",
    "risk",
    "schema_version",
    "source",
    "state",
    "strategy",
}
_METRIC_FIELDS = {
    "benchmark_ending_value",
    "benchmark_return",
    "benchmark_starting_value",
    "ending_equity_usd",
    "estimated_slippage_usd",
    "excess_return",
    "fill_count",
    "maximum_drawdown",
    "order_count",
    "rejected_order_count",
    "starting_equity_usd",
    "total_fees_usd",
    "total_return",
}
_POSITIVE_METRICS = {
    "benchmark_ending_value",
    "benchmark_starting_value",
    "ending_equity_usd",
    "starting_equity_usd",
}
_NONNEGATIVE_METRICS = {
    "estimated_slippage_usd",
    "maximum_drawdown",
    "total_fees_usd",
}
_SIGNED_METRICS = {"benchmark_return", "excess_return", "total_return"}
_COUNT_METRICS = {"fill_count", "order_count", "rejected_order_count"}
_STATE_FIELDS = {
    "completion_status",
    "final_evaluation_close_seen",
    "final_position",
    "first_eligible_evaluation_timestamp",
    "halt_reasons",
    "last_processed_evaluation_timestamp",
    "risk_halted",
    "warmup_completed",
}
_HALT_REASONS = ("daily_loss", "invalid_equity", "max_drawdown")

_AGGREGATE_FIELDS = {
    "contract_status",
    "fold_results",
    "identities",
    "protocol_name",
    "protocol_version",
    "record_type",
    "runtime_consistency",
    "schema_version",
    "summary",
}


class WalkForwardObservationError(ValueError):
    """Raised when raw or normalized walk-forward evidence is unsafe or invalid."""


def canonical_decimal(value: object) -> str:
    """Return one finite, exponent-free canonical decimal string."""

    if isinstance(value, bool):
        raise WalkForwardObservationError("boolean values are not decimal metrics")
    if isinstance(value, str) and len(value) > _MAX_DECIMAL_TEXT_CHARACTERS:
        raise WalkForwardObservationError("metric value exceeds the fixed decimal bound")
    try:
        selected = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError) as exc:
        raise WalkForwardObservationError("metric value must be a decimal") from exc
    if not selected.is_finite():
        raise WalkForwardObservationError("metric value must be finite")
    if selected == 0:
        return "0"
    if abs(selected.adjusted()) > _MAX_DECIMAL_ADJUSTED_EXPONENT:
        raise WalkForwardObservationError("metric value exceeds the fixed decimal bound")
    rendered = format(selected, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if len(rendered) > _MAX_DECIMAL_TEXT_CHARACTERS:
        raise WalkForwardObservationError("metric value exceeds the fixed decimal bound")
    return rendered


def normalize_observation(
    payload: Mapping[str, object],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Validate one sanitized observation against the immutable v1 protocol."""

    selected_bundle = bundle or load_protocol_bundle()
    if not isinstance(payload, Mapping):
        raise WalkForwardObservationError("walk-forward observation must be an object")
    _scan_untrusted_value(payload, "observation")
    _require_fields(payload, _TOP_FIELDS, "observation")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise WalkForwardObservationError("observation.schema_version is unsupported")
    if payload["protocol_version"] != PROTOCOL_VERSION:
        raise WalkForwardObservationError("observation.protocol_version is unsupported")

    fold_id = _string(payload["fold_id"], "observation.fold_id")
    try:
        fold = fold_by_id(selected_bundle, fold_id)
    except WalkForwardContractError as exc:
        raise WalkForwardObservationError("unknown walk-forward fold") from exc
    if payload["evaluation_start"] != fold.evaluation_start:
        raise WalkForwardObservationError("observation.evaluation_start differs from its fold")
    if payload["evaluation_end"] != fold.evaluation_end:
        raise WalkForwardObservationError("observation.evaluation_end differs from its fold")

    manifest = selected_bundle.manifest
    for field in ("strategy", "risk", "costs", "data", "execution"):
        observed = _mapping(payload[field], f"observation.{field}")
        if dict(observed) != manifest[field]:
            raise WalkForwardObservationError(
                f"observation.{field} differs from the fixed v1 protocol"
            )

    engine = _mapping(payload["engine"], "observation.engine")
    _require_fields(engine, {"name", "version"}, "observation.engine")
    if engine["name"] != ENGINE_NAME:
        raise WalkForwardObservationError(f"observation.engine.name must be {ENGINE_NAME}")
    engine_version = _string(engine["version"], "observation.engine.version")
    if _ENGINE_VERSION.fullmatch(engine_version) is None:
        raise WalkForwardObservationError(
            "observation.engine.version must be a safe dotted numeric version"
        )

    source = _mapping(payload["source"], "observation.source")
    _require_fields(
        source,
        {"project_source_sha256", "public_configuration_sha256"},
        "observation.source",
    )
    if source["project_source_sha256"] != selected_bundle.project_source_sha256:
        raise WalkForwardObservationError("observation project source hash has drifted")
    if source["public_configuration_sha256"] != selected_bundle.public_configuration_sha256:
        raise WalkForwardObservationError("observation public configuration hash has drifted")

    metrics = _normalize_metrics(payload["metrics"])
    _validate_metric_identities(metrics)
    state = _normalize_state(payload["state"], fold.evaluation_start, fold.evaluation_end)
    return {
        "costs": dict(manifest["costs"]),
        "data": dict(manifest["data"]),
        "engine": {"name": ENGINE_NAME, "version": engine_version},
        "evaluation_end": fold.evaluation_end,
        "evaluation_start": fold.evaluation_start,
        "execution": dict(manifest["execution"]),
        "fold_id": fold.fold_id,
        "metrics": metrics,
        "protocol_version": PROTOCOL_VERSION,
        "risk": dict(manifest["risk"]),
        "schema_version": SCHEMA_VERSION,
        "source": {
            "project_source_sha256": selected_bundle.project_source_sha256,
            "public_configuration_sha256": selected_bundle.public_configuration_sha256,
        },
        "state": state,
        "strategy": dict(manifest["strategy"]),
    }


def parse_observation_log(
    input_log: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Extract exactly one bounded canonical observation from an existing log."""

    source = Path(input_log)
    _reject_path_links(source, "input log")
    _require_regular_file(source, "input log", _MAX_INPUT_LOG_BYTES)
    try:
        raw_payload = _read_single_payload(source)
    except OSError as exc:
        raise WalkForwardObservationError("input log could not be read") from exc
    decoded = _decode_canonical_payload(raw_payload)
    return normalize_observation(decoded, bundle=bundle)


def extract_observation(
    input_log: str | Path,
    output_path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> Path:
    """Atomically write one validated observation as deterministic JSON."""

    source = Path(input_log)
    destination = Path(output_path)
    if _path_identity(source) == _path_identity(destination):
        raise WalkForwardObservationError("input log and output observation must differ")
    normalized = parse_observation_log(source, bundle=bundle)
    return _safe_atomic_json(destination, deterministic_json(normalized))


def load_observation(
    path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load one already-normalized observation with strict JSON semantics."""

    selected = Path(path)
    _reject_path_links(selected, "normalized observation")
    _require_regular_file(selected, "normalized observation", _MAX_NORMALIZED_OBSERVATION_BYTES)
    payload, raw = _load_bounded_json_object(
        selected, _MAX_NORMALIZED_OBSERVATION_BYTES, "normalized observation"
    )
    normalized = normalize_observation(payload, bundle=bundle)
    if raw != deterministic_json(normalized).encode("utf-8"):
        raise WalkForwardObservationError(
            "normalized observation must use sorted UTF-8 JSON with LF and a final newline"
        )
    return normalized


def aggregate_observations(
    observations: Sequence[Mapping[str, object]],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Build a deterministic descriptive aggregate from exactly five folds."""

    selected_bundle = bundle or load_protocol_bundle()
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise WalkForwardObservationError("walk-forward observations must be an array")
    if len(observations) != len(FOLD_IDS):
        raise WalkForwardObservationError("walk-forward aggregation requires the exact five folds")
    normalized = [
        normalize_observation(observation, bundle=selected_bundle) for observation in observations
    ]
    fold_ids = [observation["fold_id"] for observation in normalized]
    if len(fold_ids) != len(set(fold_ids)):
        raise WalkForwardObservationError("walk-forward aggregation contains a duplicate fold")
    if set(fold_ids) != set(FOLD_IDS):
        raise WalkForwardObservationError("walk-forward aggregation requires the exact five folds")
    by_id = {observation["fold_id"]: observation for observation in normalized}
    ordered = [by_id[fold_id] for fold_id in FOLD_IDS]

    strategy_returns = [_metric_decimal(item, "total_return") for item in ordered]
    benchmark_returns = [_metric_decimal(item, "benchmark_return") for item in ordered]
    excess_returns = [_metric_decimal(item, "excess_return") for item in ordered]
    drawdowns = [_metric_decimal(item, "maximum_drawdown") for item in ordered]
    fees = [_metric_decimal(item, "total_fees_usd") for item in ordered]
    completed = sum(item["state"]["completion_status"] == "completed" for item in ordered)
    versions = sorted({str(item["engine"]["version"]) for item in ordered})
    status = (
        "walk_forward_contract_complete"
        if completed == len(FOLD_IDS)
        else "walk_forward_contract_failed"
    )
    return {
        "contract_status": status,
        "fold_results": ordered,
        "identities": {
            "aggregate_schema_sha256": selected_bundle.aggregate_schema_sha256,
            "observation_schema_sha256": selected_bundle.observation_schema_sha256,
            "protocol_manifest_sha256": selected_bundle.manifest_sha256,
            "project_source_sha256": selected_bundle.project_source_sha256,
            "public_configuration_sha256": selected_bundle.public_configuration_sha256,
        },
        "protocol_name": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "record_type": "walk_forward_aggregate",
        "runtime_consistency": {
            "consistent": len(versions) == 1,
            "versions": versions,
        },
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "benchmark_beating_fold_count": sum(value > 0 for value in excess_returns),
            "completed_fold_count": completed,
            "halt_count": sum(bool(item["state"]["risk_halted"]) for item in ordered),
            "median_benchmark_return": canonical_decimal(_median(benchmark_returns)),
            "median_excess_return": canonical_decimal(_median(excess_returns)),
            "median_strategy_return": canonical_decimal(_median(strategy_returns)),
            "positive_return_fold_count": sum(value > 0 for value in strategy_returns),
            "total_fees_usd": canonical_decimal(sum(fees, Decimal("0"))),
            "total_orders": sum(int(item["metrics"]["order_count"]) for item in ordered),
            "worst_fold_return": canonical_decimal(min(strategy_returns)),
            "worst_maximum_drawdown": canonical_decimal(max(drawdowns)),
        },
    }


def aggregate_observation_files(
    paths: Sequence[str | Path],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load and aggregate five normalized observation files offline."""

    selected_bundle = bundle or load_protocol_bundle()
    _require_exact_five_paths(paths)
    return aggregate_observations(
        [load_observation(path, bundle=selected_bundle) for path in paths],
        bundle=selected_bundle,
    )


def normalize_aggregate_record(
    payload: Mapping[str, object],
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Recompute and verify every supplied aggregate status and summary field."""

    if not isinstance(payload, Mapping):
        raise WalkForwardObservationError("walk-forward aggregate must be an object")
    _scan_untrusted_value(payload, "aggregate")
    _require_fields(payload, _AGGREGATE_FIELDS, "aggregate")
    folds = payload["fold_results"]
    if isinstance(folds, (str, bytes)) or not isinstance(folds, Sequence):
        raise WalkForwardObservationError("aggregate.fold_results must be an array")
    expected = aggregate_observations(folds, bundle=bundle)
    if dict(payload) != expected:
        raise WalkForwardObservationError(
            "aggregate status, identities, runtime report, or summary is not derived from its folds"
        )
    return expected


def write_aggregate_record(
    observations: Sequence[Mapping[str, object]],
    output_path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> Path:
    """Atomically write a canonical aggregate from exactly five observations."""

    aggregate = aggregate_observations(observations, bundle=bundle)
    return _safe_atomic_json(Path(output_path), deterministic_json(aggregate))


def write_aggregate_files(
    paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> Path:
    """Load five normalized folds and atomically write their aggregate."""

    _require_exact_five_paths(paths)
    destination = Path(output_path)
    if any(_path_identity(Path(path)) == _path_identity(destination) for path in paths):
        raise WalkForwardObservationError("aggregate output must differ from every fold input")
    selected_bundle = bundle or load_protocol_bundle()
    observations = [load_observation(path, bundle=selected_bundle) for path in paths]
    return write_aggregate_record(observations, destination, bundle=selected_bundle)


def load_aggregate_record(
    path: str | Path,
    *,
    bundle: ProtocolBundle | None = None,
) -> dict[str, Any]:
    """Load one canonical aggregate and recompute all derived fields."""

    selected = Path(path)
    _reject_path_links(selected, "aggregate record")
    _require_regular_file(selected, "aggregate record", _MAX_AGGREGATE_RECORD_BYTES)
    payload, raw = _load_bounded_json_object(
        selected, _MAX_AGGREGATE_RECORD_BYTES, "aggregate record"
    )
    normalized = normalize_aggregate_record(payload, bundle=bundle)
    if raw != deterministic_json(normalized).encode("utf-8"):
        raise WalkForwardObservationError(
            "aggregate record must use sorted UTF-8 JSON with LF and a final newline"
        )
    return normalized


def _normalize_metrics(value: object) -> dict[str, object]:
    metrics = _mapping(value, "observation.metrics")
    _require_fields(metrics, _METRIC_FIELDS, "observation.metrics")
    normalized: dict[str, object] = {}
    for field in sorted(_POSITIVE_METRICS | _NONNEGATIVE_METRICS | _SIGNED_METRICS):
        selected = _parse_decimal_string(metrics[field], f"observation.metrics.{field}")
        if field in _POSITIVE_METRICS and selected <= 0:
            raise WalkForwardObservationError(f"observation.metrics.{field} must be positive")
        if field in _NONNEGATIVE_METRICS and selected < 0:
            raise WalkForwardObservationError(f"observation.metrics.{field} must be non-negative")
        if field == "maximum_drawdown" and selected > 1:
            raise WalkForwardObservationError(
                "observation.metrics.maximum_drawdown cannot exceed one"
            )
        normalized[field] = str(metrics[field])
    for field in sorted(_COUNT_METRICS):
        selected = metrics[field]
        if isinstance(selected, bool) or not isinstance(selected, int) or selected < 0:
            raise WalkForwardObservationError(
                f"observation.metrics.{field} must be a non-negative integer"
            )
        normalized[field] = selected
    return normalized


def _validate_metric_identities(metrics: Mapping[str, object]) -> None:
    starting_equity = _parse_decimal_string(
        metrics["starting_equity_usd"], "observation.metrics.starting_equity_usd"
    )
    ending_equity = _parse_decimal_string(
        metrics["ending_equity_usd"], "observation.metrics.ending_equity_usd"
    )
    benchmark_start = _parse_decimal_string(
        metrics["benchmark_starting_value"],
        "observation.metrics.benchmark_starting_value",
    )
    benchmark_end = _parse_decimal_string(
        metrics["benchmark_ending_value"], "observation.metrics.benchmark_ending_value"
    )
    if starting_equity != Decimal("100000"):
        raise WalkForwardObservationError("observation starting capital differs from v1")

    total_return = ending_equity / starting_equity - Decimal("1")
    benchmark_return = benchmark_end / benchmark_start - Decimal("1")
    expected = {
        "benchmark_return": canonical_decimal(benchmark_return),
        "excess_return": canonical_decimal(total_return - benchmark_return),
        "total_return": canonical_decimal(total_return),
    }
    if any(metrics[field] != value for field, value in expected.items()):
        raise WalkForwardObservationError("observation return metrics are internally inconsistent")


def _normalize_state(value: object, evaluation_start: str, evaluation_end: str) -> dict[str, Any]:
    state = _mapping(value, "observation.state")
    _require_fields(state, _STATE_FIELDS, "observation.state")
    completion = state["completion_status"]
    if completion not in {"completed", "failed"}:
        raise WalkForwardObservationError("observation.state.completion_status is unsupported")
    warmup = state["warmup_completed"]
    halted = state["risk_halted"]
    final_close_seen = state["final_evaluation_close_seen"]
    if any(type(value) is not bool for value in (warmup, halted, final_close_seen)):
        raise WalkForwardObservationError(
            "observation warmup, halt, and boundary states must be booleans"
        )
    if completion == "completed" and not warmup:
        raise WalkForwardObservationError("a completed fold must have completed warmup")
    if completion == "completed" and not final_close_seen:
        raise WalkForwardObservationError(
            "a completed fold must include its final evaluation close"
        )

    reasons = state["halt_reasons"]
    if isinstance(reasons, (str, bytes)) or not isinstance(reasons, Sequence):
        raise WalkForwardObservationError("observation.state.halt_reasons must be an array")
    if any(reason not in _HALT_REASONS for reason in reasons):
        raise WalkForwardObservationError("observation.state.halt_reasons contains raw text")
    normalized_reasons = sorted(set(str(reason) for reason in reasons))
    if list(reasons) != normalized_reasons:
        raise WalkForwardObservationError(
            "observation.state.halt_reasons must be unique and sorted"
        )
    if halted != bool(normalized_reasons):
        raise WalkForwardObservationError(
            "observation.state.risk_halted disagrees with halt_reasons"
        )

    first = _parse_timestamp(
        state["first_eligible_evaluation_timestamp"],
        "observation.state.first_eligible_evaluation_timestamp",
    )
    last = _parse_timestamp(
        state["last_processed_evaluation_timestamp"],
        "observation.state.last_processed_evaluation_timestamp",
    )
    if first > last:
        raise WalkForwardObservationError("observation evaluation timestamps are reversed")
    start_date = date.fromisoformat(evaluation_start)
    end_date = date.fromisoformat(evaluation_end)
    if first.date() < start_date or first.date() > end_date:
        raise WalkForwardObservationError("first eligible timestamp falls outside its fold")
    if last.date() < start_date or last.date() > end_date:
        raise WalkForwardObservationError("last processed timestamp falls outside its fold")

    final = _mapping(state["final_position"], "observation.state.final_position")
    _require_fields(final, {"quantity", "state"}, "observation.state.final_position")
    position_state = final["state"]
    if position_state not in {"cash", "long"}:
        raise WalkForwardObservationError("observation final position must be cash or long")
    quantity = _parse_decimal_string(final["quantity"], "observation.state.final_position.quantity")
    if quantity < 0:
        raise WalkForwardObservationError("observation final position cannot be short")
    if (position_state == "cash") != (quantity == 0):
        raise WalkForwardObservationError("final position state disagrees with quantity")
    return {
        "completion_status": str(completion),
        "final_evaluation_close_seen": final_close_seen,
        "final_position": {
            "quantity": str(final["quantity"]),
            "state": str(position_state),
        },
        "first_eligible_evaluation_timestamp": _format_timestamp(first),
        "halt_reasons": normalized_reasons,
        "last_processed_evaluation_timestamp": _format_timestamp(last),
        "risk_halted": halted,
        "warmup_completed": warmup,
    }


def _read_single_payload(input_log: Path) -> bytes:
    selected: bytes | None = None
    bytes_read = 0
    with input_log.open("rb") as handle:
        while True:
            raw_line, bytes_read = _bounded_readline(handle, bytes_read)
            if not raw_line:
                break
            if len(raw_line) > _MAX_LOG_LINE_BYTES:
                prefixed = raw_line.startswith(_PREFIX_BYTES)
                while raw_line and not raw_line.endswith(b"\n"):
                    raw_line, bytes_read = _bounded_readline(handle, bytes_read)
                if prefixed:
                    raise WalkForwardObservationError(
                        "walk-forward observation payload exceeds the fixed size limit"
                    )
                continue
            if not raw_line.startswith(_PREFIX_BYTES):
                continue
            if selected is not None:
                raise WalkForwardObservationError(
                    "input log must contain exactly one walk-forward observation"
                )
            initial = _strip_line_ending(raw_line)[len(_PREFIX_BYTES) :]
            if not initial:
                raise WalkForwardObservationError("walk-forward observation payload is empty")
            selected, bytes_read = _read_wrapped_payload(initial, handle, bytes_read)
    if selected is None:
        raise WalkForwardObservationError(
            "input log must contain exactly one walk-forward observation"
        )
    return selected


def _read_wrapped_payload(initial: bytes, handle: BinaryIO, bytes_read: int) -> tuple[bytes, int]:
    payload = bytearray(initial)
    if len(payload) > MAX_OBSERVATION_PAYLOAD_BYTES:
        raise WalkForwardObservationError(
            "walk-forward observation payload exceeds the fixed size limit"
        )
    while not _json_object_is_complete(payload):
        raw_line, bytes_read = _bounded_readline(handle, bytes_read)
        if not raw_line:
            break
        payload.extend(_strip_line_ending(raw_line))
        if len(payload) > MAX_OBSERVATION_PAYLOAD_BYTES:
            raise WalkForwardObservationError(
                "walk-forward observation payload exceeds the fixed size limit"
            )
    return bytes(payload), bytes_read


def _bounded_readline(handle: BinaryIO, bytes_read: int) -> tuple[bytes, int]:
    raw_line = handle.readline(_MAX_LOG_LINE_BYTES + 1)
    total = bytes_read + len(raw_line)
    if total > _MAX_INPUT_LOG_BYTES:
        raise WalkForwardObservationError("input log exceeds the fixed total byte limit")
    return raw_line, total


def _strip_line_ending(line: bytes) -> bytes:
    if line.endswith(b"\n"):
        line = line[:-1]
    if line.endswith(b"\r"):
        line = line[:-1]
    return line


def _json_object_is_complete(payload: bytes | bytearray) -> bool:
    if not payload or payload[0] != ord("{"):
        return True
    depth = 0
    in_string = False
    escaped = False
    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte in (ord("{"), ord("[")):
            depth += 1
        elif byte in (ord("}"), ord("]")):
            depth -= 1
            if depth <= 0:
                return True
    return False


def _decode_canonical_payload(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WalkForwardObservationError(
            "walk-forward observation payload must be valid UTF-8"
        ) from exc

    def reject_constant(value: str) -> None:
        raise WalkForwardObservationError(
            f"walk-forward observation contains non-finite JSON value {value}"
        )

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WalkForwardObservationError(
                    "walk-forward observation contains a duplicate JSON key"
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise WalkForwardObservationError(
            "walk-forward observation payload is malformed JSON"
        ) from exc
    except (OverflowError, RecursionError, ValueError) as exc:
        if isinstance(exc, WalkForwardObservationError):
            raise
        raise WalkForwardObservationError(
            "walk-forward observation payload contains an invalid JSON value"
        ) from exc
    if not isinstance(decoded, dict):
        raise WalkForwardObservationError("walk-forward observation must be a JSON object")
    _reject_non_finite(decoded)
    try:
        canonical = compact_json(decoded)
    except (RecursionError, TypeError, ValueError) as exc:
        raise WalkForwardObservationError(
            "walk-forward observation payload contains an invalid JSON value"
        ) from exc
    if canonical != text:
        raise WalkForwardObservationError(
            "walk-forward observation payload must use canonical compact JSON"
        )
    return decoded


def _reject_non_finite(value: object) -> None:
    pending = [value]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        if inspected > _MAX_UNTRUSTED_NODES:
            raise WalkForwardObservationError("JSON value exceeds the fixed structural limit")
        if isinstance(current, float) and not isfinite(current):
            raise WalkForwardObservationError(
                "walk-forward observation contains a non-finite number"
            )
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend(current)


def _scan_untrusted_value(value: object, path: str) -> None:
    pending = [value]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        if inspected > _MAX_UNTRUSTED_NODES:
            raise WalkForwardObservationError(f"{path} exceeds the fixed structural limit")
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                if not isinstance(raw_key, str):
                    raise WalkForwardObservationError(f"{path} contains a non-string field name")
                if _FORBIDDEN_KEY_TOKENS.intersection(_field_tokens(raw_key)):
                    raise WalkForwardObservationError(f"{path} contains a forbidden identity field")
                pending.append(child)
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend(current)
            continue
        if isinstance(current, float) and not isfinite(current):
            raise WalkForwardObservationError(f"{path} contains a non-finite number")
        if isinstance(current, str):
            if any(ord(character) < 32 for character in current):
                raise WalkForwardObservationError(f"{path} contains control characters")
            if _EMAIL.search(current) or "@" in current:
                raise WalkForwardObservationError(f"{path} contains an email address")
            if "/" in current or "\\" in current:
                raise WalkForwardObservationError(f"{path} contains machine path or URL text")
            if "://" in current or _SECRET_VALUE.search(current):
                raise WalkForwardObservationError(f"{path} contains URL or credential text")
            if current.startswith("-----BEGIN ") or current.casefold().startswith("bearer "):
                raise WalkForwardObservationError(f"{path} contains authentication material")
            if _IDENTITY_TEXT.search(current):
                raise WalkForwardObservationError(f"{path} contains identity-bearing text")


def _field_tokens(value: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", separated).strip("-").lower()
    tokens = set(normalized.split("-")) if normalized else set()
    compact = normalized.replace("-", "")
    if compact:
        tokens.add(compact)
    return tokens


def _load_bounded_json_object(
    path: Path, max_bytes: int, label: str
) -> tuple[dict[str, Any], bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise WalkForwardObservationError(f"{label} could not be loaded") from exc
    if len(raw) > max_bytes:
        raise WalkForwardObservationError(f"{label} exceeds the fixed byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WalkForwardObservationError(f"{label} must be valid UTF-8") from exc

    def reject_constant(_value: str) -> None:
        raise WalkForwardObservationError(f"{label} contains a non-finite JSON value")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WalkForwardObservationError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except WalkForwardObservationError:
        raise
    except (json.JSONDecodeError, OverflowError, RecursionError, ValueError) as exc:
        raise WalkForwardObservationError(f"{label} contains invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WalkForwardObservationError(f"{label} must contain a JSON object")
    _reject_non_finite(payload)
    return payload, raw


def _safe_atomic_json(destination: Path, serialized: str) -> Path:
    if not destination.name or destination.suffix.lower() != ".json":
        raise WalkForwardObservationError("output path must name a JSON file")
    _reject_path_links(destination, "output destination")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WalkForwardObservationError("output directory could not be prepared") from exc
    _reject_path_links(destination, "output destination")
    try:
        return atomic_write_text(destination, serialized)
    except OSError as exc:
        raise WalkForwardObservationError("output JSON could not be written") from exc


def _reject_path_links(path: Path, label: str) -> None:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WalkForwardObservationError(f"{label} could not be inspected safely") from exc
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        is_reparse_point = bool(os.name == "nt" and reparse_flag and attributes & reparse_flag)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
            if label == "output destination":
                message = (
                    "output destination, root, and parent directories must not be symlinks "
                    "or reparse points"
                )
            elif label == "input log":
                message = "input log must not be a symlink or traverse symlinked directories"
            else:
                message = f"{label} must not be a symlink or traverse symlinked directories"
            raise WalkForwardObservationError(message)


def _require_regular_file(path: Path, label: str, max_bytes: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise WalkForwardObservationError(f"{label} could not be inspected safely") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise WalkForwardObservationError(f"{label} must be a regular file")
    if metadata.st_size > max_bytes:
        raise WalkForwardObservationError(f"{label} exceeds the fixed byte limit")


def _path_identity(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WalkForwardObservationError("artifact path could not be resolved safely") from exc


def _require_exact_five_paths(paths: Sequence[str | Path]) -> None:
    if (
        isinstance(paths, (str, bytes))
        or not isinstance(paths, Sequence)
        or len(paths) != len(FOLD_IDS)
    ):
        raise WalkForwardObservationError(
            "walk-forward aggregation requires exactly five artifact paths"
        )


def _parse_decimal_string(value: object, path: str) -> Decimal:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_DECIMAL_TEXT_CHARACTERS
        or _CANONICAL_DECIMAL_TEXT.fullmatch(value) is None
    ):
        raise WalkForwardObservationError(f"{path} must be a finite canonical decimal string")
    try:
        selected = Decimal(value)
    except DecimalException as exc:
        raise WalkForwardObservationError(
            f"{path} must be a finite canonical decimal string"
        ) from exc
    if not selected.is_finite() or canonical_decimal(selected) != value:
        raise WalkForwardObservationError(f"{path} must be a finite canonical decimal string")
    return selected


def _metric_decimal(observation: Mapping[str, Any], field: str) -> Decimal:
    return _parse_decimal_string(observation["metrics"][field], f"metrics.{field}")


def _median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise WalkForwardObservationError("cannot summarize an empty metric sequence")
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _parse_timestamp(value: object, path: str) -> datetime:
    selected = _string(value, path)
    try:
        parsed = datetime.strptime(selected, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise WalkForwardObservationError(
            f"{path} must use canonical UTC seconds ending in Z"
        ) from exc
    if _format_timestamp(parsed) != selected:
        raise WalkForwardObservationError(f"{path} must use canonical UTC seconds ending in Z")
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WalkForwardObservationError(f"{path} must be an object")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise WalkForwardObservationError(f"{path} must be a non-empty string")
    return value


def _require_fields(value: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise WalkForwardObservationError(f"{path} fields differ from the fixed contract")


__all__ = [
    "WalkForwardObservationError",
    "aggregate_observation_files",
    "aggregate_observations",
    "canonical_decimal",
    "extract_observation",
    "load_aggregate_record",
    "load_observation",
    "normalize_aggregate_record",
    "normalize_observation",
    "parse_observation_log",
    "write_aggregate_files",
    "write_aggregate_record",
]
