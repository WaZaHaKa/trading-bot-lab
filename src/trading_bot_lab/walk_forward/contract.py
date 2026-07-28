"""Immutable identities and typed loader for walk-forward protocol v1."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from trading_bot_lab.parity.contract import deterministic_json, load_json_object

PROTOCOL_NAME = "fixed_parameter_rolling_walk_forward"
PROTOCOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
OBSERVATION_PREFIX = "TRADING_BOT_LAB_LEAN_WALK_FORWARD_V1:"
MAX_OBSERVATION_PAYLOAD_BYTES = 16_384
ENGINE_NAME = "quantconnect_lean"
ENGINE_VERSION_PATTERN = r"^[0-9]{1,9}(?:\.[0-9]{1,9}){1,5}$"

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = REPOSITORY_ROOT / "contracts" / "walk-forward" / "v1"
PROJECT_DIRECTORY = REPOSITORY_ROOT / "lean-workspace" / "Strategies" / "WalkForwardMovingAverageV1"
PROTOCOL_PATH = CONTRACT_DIRECTORY / "protocol.json"
PROTOCOL_SCHEMA_PATH = CONTRACT_DIRECTORY / "protocol.schema.json"
OBSERVATION_SCHEMA_PATH = CONTRACT_DIRECTORY / "observation.schema.json"
AGGREGATE_SCHEMA_PATH = CONTRACT_DIRECTORY / "aggregate-record.schema.json"

FOLDS: tuple[tuple[str, str, str], ...] = (
    ("spy-2021", "2021-01-01", "2021-12-31"),
    ("spy-2022", "2022-01-01", "2022-12-31"),
    ("spy-2023", "2023-01-01", "2023-12-31"),
    ("spy-2024", "2024-01-01", "2024-12-31"),
    ("spy-2025", "2025-01-01", "2025-12-31"),
)
FOLD_IDS = tuple(fold[0] for fold in FOLDS)

EXPECTED_STRATEGY = {
    "fast_period": 20,
    "slow_period": 50,
    "target_weight": "0.10",
    "warmup_bars": 50,
}
EXPECTED_RISK = {
    "account_type": "cash",
    "automatic_liquidation": False,
    "leverage": "1",
    "long_only": True,
    "max_daily_loss": "0.02",
    "max_drawdown": "0.05",
    "max_gross_exposure": "0.30",
    "max_position_weight": "0.10",
}
EXPECTED_COSTS = {
    "fee_bps": "1.0",
    "minimum_fee_usd": "1.0",
    "slippage_bps": "2.0",
}
EXPECTED_DATA = {
    "data_normalization": "adjusted",
    "resolution": "daily",
    "symbol": "SPY",
}
EXPECTED_EXECUTION = {
    "fill_forward": False,
    "final_signal_expires_without_next_open": True,
    "first_eligible_signal": "completed_trailing_history_only",
    "orders_during_warmup": False,
    "signal_time": "completed_daily_close",
    "timing": "next_market_open",
}
EXPECTED_CAPITAL = {"initial_cash_usd": "100000"}
EXPECTED_BENCHMARK = {
    "costs_included": False,
    "ending_value": "last_adjusted_close_in_evaluation_interval",
    "methodology": "adjusted_spy_close_to_close",
    "starting_value": "first_adjusted_close_in_evaluation_interval",
}
PERMITTED_RESULT_FIELDS = (
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
)
PROHIBITED_IDENTITY_FIELDS = (
    "account_email",
    "account_id",
    "backtest_id",
    "billing_data",
    "cloud_id",
    "credentials",
    "email",
    "invitation_url",
    "license",
    "machine_path",
    "organization_id",
    "owner",
    "project_id",
    "raw_engine_logs",
    "raw_order_ids",
    "secret",
    "subscription",
    "token",
    "url",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SLOT = re.compile(rb'(?m)^PROJECT_SOURCE_SHA256 = "([0-9a-f]{64})"$')


class WalkForwardContractError(ValueError):
    """Raised when the immutable v1 protocol or one of its identities drifts."""


@dataclass(frozen=True)
class Fold:
    fold_id: str
    evaluation_start: str
    evaluation_end: str

    def as_dict(self) -> dict[str, str]:
        return {
            "evaluation_end": self.evaluation_end,
            "evaluation_start": self.evaluation_start,
            "fold_id": self.fold_id,
        }


@dataclass(frozen=True)
class ProtocolBundle:
    manifest: dict[str, Any]
    manifest_sha256: str
    protocol_schema_sha256: str
    observation_schema_sha256: str
    aggregate_schema_sha256: str
    project_source_sha256: str
    public_configuration_sha256: str
    folds: tuple[Fold, ...]


def sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def compact_json(payload: Mapping[str, object]) -> str:
    """Return canonical compact JSON for log payloads and hashed identities."""

    return json.dumps(
        dict(payload),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def normalized_project_source_bytes(path: str | Path) -> bytes:
    """Zero the one documented self-identity slot before hashing source bytes."""

    raw = Path(path).read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise WalkForwardContractError("project source must be UTF-8 with LF and a final newline")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WalkForwardContractError("project source must be valid UTF-8") from exc
    matches = tuple(_SOURCE_SLOT.finditer(raw))
    if len(matches) != 1:
        raise WalkForwardContractError("project source must contain exactly one identity slot")
    start, end = matches[0].span(1)
    return raw[:start] + (b"0" * 64) + raw[end:]


def project_source_sha256(path: str | Path | None = None) -> str:
    selected = Path(path) if path is not None else PROJECT_DIRECTORY / "main.py"
    return sha256_bytes(normalized_project_source_bytes(selected))


def public_configuration_sha256(path: str | Path | None = None) -> str:
    selected = Path(path) if path is not None else PROJECT_DIRECTORY / "config.json"
    raw = selected.read_bytes()
    if b"\r" in raw or not raw.endswith(b"\n"):
        raise WalkForwardContractError(
            "public configuration must be UTF-8 with LF and a final newline"
        )
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WalkForwardContractError("public configuration must be valid UTF-8") from exc
    return sha256_bytes(raw)


def load_protocol_bundle(
    path: str | Path = PROTOCOL_PATH,
    *,
    verify_project_files: bool = True,
) -> ProtocolBundle:
    """Load the exact v1 manifest and fail closed on any protocol drift."""

    selected = Path(path)
    try:
        manifest, raw = load_json_object(selected)
    except (OSError, ValueError) as exc:
        raise WalkForwardContractError(str(exc)) from exc
    try:
        canonical = deterministic_json(manifest).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WalkForwardContractError("protocol manifest is not deterministic JSON") from exc
    if raw != canonical:
        raise WalkForwardContractError(
            "protocol manifest must use sorted deterministic JSON, UTF-8, LF, and a final newline"
        )

    _require_fields(
        manifest,
        {
            "aggregate_schema_sha256",
            "benchmark",
            "capital",
            "costs",
            "data",
            "execution",
            "folds",
            "observation_schema_sha256",
            "permitted_result_fields",
            "prohibited_identity_fields",
            "project",
            "protocol_name",
            "protocol_schema_sha256",
            "protocol_version",
            "required_engine_provenance",
            "risk",
            "schema_version",
            "strategy",
        },
        "protocol",
    )
    expected_scalars = {
        "protocol_name": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    for key, expected in expected_scalars.items():
        if manifest[key] != expected:
            raise WalkForwardContractError(f"protocol.{key} must be {expected!r}")
    _require_exact_mapping(manifest["strategy"], EXPECTED_STRATEGY, "protocol.strategy")
    _require_exact_mapping(manifest["risk"], EXPECTED_RISK, "protocol.risk")
    _require_exact_mapping(manifest["costs"], EXPECTED_COSTS, "protocol.costs")
    _require_exact_mapping(manifest["data"], EXPECTED_DATA, "protocol.data")
    _require_exact_mapping(manifest["execution"], EXPECTED_EXECUTION, "protocol.execution")
    _require_exact_mapping(manifest["capital"], EXPECTED_CAPITAL, "protocol.capital")
    _require_exact_mapping(manifest["benchmark"], EXPECTED_BENCHMARK, "protocol.benchmark")
    folds = _parse_folds(manifest["folds"])

    schema_hashes = {
        "protocol_schema_sha256": _canonical_json_sha256(PROTOCOL_SCHEMA_PATH, "protocol schema"),
        "observation_schema_sha256": _canonical_json_sha256(
            OBSERVATION_SCHEMA_PATH, "observation schema"
        ),
        "aggregate_schema_sha256": _canonical_json_sha256(
            AGGREGATE_SCHEMA_PATH, "aggregate schema"
        ),
    }
    for key, observed in schema_hashes.items():
        _require_sha256(manifest[key], f"protocol.{key}")
        if manifest[key] != observed:
            raise WalkForwardContractError(f"protocol.{key} does not match exact schema bytes")

    project = _mapping(manifest["project"], "protocol.project")
    _require_fields(
        project,
        {
            "algorithm_name",
            "project_name",
            "public_configuration_hash_method",
            "public_configuration_sha256",
            "source_hash_method",
            "source_identity_slot",
            "source_sha256",
        },
        "protocol.project",
    )
    expected_project = {
        "algorithm_name": "WalkForwardMovingAverageV1",
        "project_name": "Strategies/WalkForwardMovingAverageV1",
        "public_configuration_hash_method": "sha256_exact_utf8_lf_bytes",
        "source_hash_method": "sha256_utf8_lf_with_source_identity_slot_zeroed",
        "source_identity_slot": "PROJECT_SOURCE_SHA256",
    }
    for key, expected in expected_project.items():
        if project[key] != expected:
            raise WalkForwardContractError(f"protocol.project.{key} must be {expected!r}")
    _require_sha256(project["source_sha256"], "protocol.project.source_sha256")
    _require_sha256(
        project["public_configuration_sha256"],
        "protocol.project.public_configuration_sha256",
    )
    source_hash = str(project["source_sha256"])
    config_hash = str(project["public_configuration_sha256"])
    if verify_project_files:
        observed_source = project_source_sha256()
        observed_config = public_configuration_sha256()
        if source_hash != observed_source:
            raise WalkForwardContractError(
                "protocol.project.source_sha256 does not match normalized project source"
            )
        if config_hash != observed_config:
            raise WalkForwardContractError(
                "protocol.project.public_configuration_sha256 does not match exact config bytes"
            )
        source_text = (PROJECT_DIRECTORY / "main.py").read_text(encoding="utf-8")
        if f'PROJECT_SOURCE_SHA256 = "{source_hash}"' not in source_text:
            raise WalkForwardContractError("project source identity constant differs from manifest")
        if f'PUBLIC_CONFIGURATION_SHA256 = "{config_hash}"' not in source_text:
            raise WalkForwardContractError(
                "project public-configuration identity constant differs from manifest"
            )

    engine = _mapping(manifest["required_engine_provenance"], "protocol.engine")
    expected_engine = {
        "max_observation_payload_bytes": MAX_OBSERVATION_PAYLOAD_BYTES,
        "name": ENGINE_NAME,
        "observation_prefix": OBSERVATION_PREFIX,
        "version_pattern": ENGINE_VERSION_PATTERN,
    }
    if dict(engine) != expected_engine:
        raise WalkForwardContractError("protocol.required_engine_provenance has drifted")

    _require_exact_string_array(
        manifest["permitted_result_fields"],
        PERMITTED_RESULT_FIELDS,
        "permitted_result_fields",
    )
    _require_exact_string_array(
        manifest["prohibited_identity_fields"],
        PROHIBITED_IDENTITY_FIELDS,
        "prohibited_identity_fields",
    )
    return ProtocolBundle(
        manifest=manifest,
        manifest_sha256=sha256_bytes(raw),
        protocol_schema_sha256=schema_hashes["protocol_schema_sha256"],
        observation_schema_sha256=schema_hashes["observation_schema_sha256"],
        aggregate_schema_sha256=schema_hashes["aggregate_schema_sha256"],
        project_source_sha256=source_hash,
        public_configuration_sha256=config_hash,
        folds=folds,
    )


def fold_by_id(bundle: ProtocolBundle, fold_id: str) -> Fold:
    for fold in bundle.folds:
        if fold.fold_id == fold_id:
            return fold
    raise WalkForwardContractError(f"unknown walk-forward fold: {fold_id!r}")


def _parse_folds(value: object) -> tuple[Fold, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WalkForwardContractError("protocol.folds must be an array")
    parsed: list[Fold] = []
    for index, raw in enumerate(value):
        fold = _mapping(raw, f"protocol.folds[{index}]")
        _require_fields(
            fold,
            {"evaluation_end", "evaluation_start", "fold_id"},
            f"protocol.folds[{index}]",
        )
        if not all(isinstance(fold[key], str) for key in fold):
            raise WalkForwardContractError(f"protocol.folds[{index}] values must be strings")
        parsed.append(
            Fold(
                fold_id=str(fold["fold_id"]),
                evaluation_start=str(fold["evaluation_start"]),
                evaluation_end=str(fold["evaluation_end"]),
            )
        )
        try:
            start = date.fromisoformat(parsed[-1].evaluation_start)
            end = date.fromisoformat(parsed[-1].evaluation_end)
        except ValueError as exc:
            raise WalkForwardContractError(
                f"protocol.folds[{index}] dates must use canonical YYYY-MM-DD"
            ) from exc
        if start.isoformat() != parsed[-1].evaluation_start:
            raise WalkForwardContractError(
                f"protocol.folds[{index}].evaluation_start is not canonical"
            )
        if end.isoformat() != parsed[-1].evaluation_end or end < start:
            raise WalkForwardContractError(
                f"protocol.folds[{index}] has an invalid evaluation interval"
            )
    expected = tuple(Fold(*values) for values in FOLDS)
    if tuple(parsed) != expected:
        raise WalkForwardContractError("protocol.folds must equal the ordered five-fold v1 set")
    if len({fold.fold_id for fold in parsed}) != len(parsed):
        raise WalkForwardContractError("protocol.folds contains a duplicate fold ID")
    for prior, current in zip(parsed, parsed[1:], strict=False):
        if prior.evaluation_end >= current.evaluation_start:
            raise WalkForwardContractError("protocol.folds evaluation intervals overlap")
    return tuple(parsed)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WalkForwardContractError(f"{path} must be an object")
    return value


def _require_fields(value: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise WalkForwardContractError(
            f"{path} fields differ; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )


def _require_exact_mapping(value: object, expected: Mapping[str, object], path: str) -> None:
    selected = _mapping(value, path)
    if dict(selected) != dict(expected):
        raise WalkForwardContractError(f"{path} differs from the fixed v1 contract")


def _require_sha256(value: object, path: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise WalkForwardContractError(f"{path} must be a lowercase SHA-256 digest")


def _require_exact_string_array(
    value: object,
    expected: tuple[str, ...],
    path: str,
) -> None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WalkForwardContractError(f"protocol.{path} must be an array")
    if not value or any(not isinstance(item, str) or not item for item in value):
        raise WalkForwardContractError(f"protocol.{path} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise WalkForwardContractError(f"protocol.{path} contains a duplicate")
    if tuple(value) != expected:
        raise WalkForwardContractError(f"protocol.{path} differs from the fixed v1 contract")


def _canonical_json_sha256(path: Path, label: str) -> str:
    try:
        payload, raw = load_json_object(path)
        canonical = deterministic_json(payload).encode("utf-8")
    except (OSError, TypeError, ValueError) as exc:
        raise WalkForwardContractError(f"{label} could not be validated") from exc
    if raw != canonical:
        raise WalkForwardContractError(
            f"{label} must use sorted deterministic JSON, UTF-8, LF, and a final newline"
        )
    return sha256_bytes(raw)


__all__ = [
    "AGGREGATE_SCHEMA_PATH",
    "CONTRACT_DIRECTORY",
    "ENGINE_NAME",
    "ENGINE_VERSION_PATTERN",
    "FOLDS",
    "FOLD_IDS",
    "EXPECTED_BENCHMARK",
    "MAX_OBSERVATION_PAYLOAD_BYTES",
    "OBSERVATION_PREFIX",
    "OBSERVATION_SCHEMA_PATH",
    "PROJECT_DIRECTORY",
    "PROTOCOL_NAME",
    "PROTOCOL_PATH",
    "PROTOCOL_SCHEMA_PATH",
    "PROTOCOL_VERSION",
    "PERMITTED_RESULT_FIELDS",
    "PROHIBITED_IDENTITY_FIELDS",
    "SCHEMA_VERSION",
    "Fold",
    "ProtocolBundle",
    "WalkForwardContractError",
    "compact_json",
    "fold_by_id",
    "load_protocol_bundle",
    "normalized_project_source_bytes",
    "project_source_sha256",
    "public_configuration_sha256",
    "sha256_bytes",
]
