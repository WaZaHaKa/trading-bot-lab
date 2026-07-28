"""Load and validate the immutable files that define parity contract v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

CONTRACT_NAME = "trading_bot_lab_cross_engine_parity"
CONTRACT_VERSION = "1.0.0"
TRACE_SCHEMA_VERSION = "1.0.0"
SCENARIO_MANIFEST_VERSION = "1.0.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = REPOSITORY_ROOT / "contracts" / "parity" / "v1"
DEFAULT_SCENARIO_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "parity" / "v1" / "scenario.json"


class ParityContractError(ValueError):
    """Raised when a versioned parity contract or scenario is invalid."""


@dataclass(frozen=True)
class ContractBundle:
    """Loaded contract documents and exact-byte identities."""

    contract: dict[str, Any]
    contract_sha256: str
    scenario_schema_sha256: str
    trace_schema_sha256: str


@dataclass(frozen=True)
class ScenarioBundle:
    """Loaded scenario manifest, fixture path, and exact-byte identities."""

    manifest: dict[str, Any]
    manifest_path: Path
    manifest_sha256: str
    fixture_path: Path
    fixture_sha256: str


def sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def sha256_path(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def load_json_object(path: str | Path) -> tuple[dict[str, Any], bytes]:
    """Read strict UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""

    selected = Path(path)
    raw = selected.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParityContractError(f"{selected.name} must be valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ParityContractError(f"{selected.name} contains non-finite JSON value {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ParityContractError(f"{selected.name} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ParityContractError(f"{selected.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ParityContractError(f"{selected.name} must contain a JSON object")
    return payload, raw


def load_contract_bundle() -> ContractBundle:
    contract_path = CONTRACT_DIRECTORY / "contract.json"
    scenario_schema_path = CONTRACT_DIRECTORY / "scenario.schema.json"
    trace_schema_path = CONTRACT_DIRECTORY / "trace.schema.json"
    contract, raw = load_json_object(contract_path)
    expected = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "scenario_manifest_version": SCENARIO_MANIFEST_VERSION,
        "execution_timing": "next_bar_open",
        "numeric_representation": "canonical_decimal_string",
        "local_oracle_provenance": "local_python_oracle_observation",
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            raise ParityContractError(f"contract.json has unsupported {key}")
    accepted = contract.get("accepted_candidate_provenance")
    if accepted != ["lean_engine_observation", "contract_fixture_not_engine_observation"]:
        raise ParityContractError("contract.json has unsupported candidate provenance values")
    tolerances = contract.get("tolerances")
    if not isinstance(tolerances, dict) or set(tolerances) != {
        "money",
        "price",
        "quantity",
        "ratio",
    }:
        raise ParityContractError("contract.json must define every v1 tolerance category")
    for name, value in tolerances.items():
        parsed = parse_decimal_string(value, field=f"tolerances.{name}")
        if parsed < 0:
            raise ParityContractError(f"tolerances.{name} must be non-negative")
    return ContractBundle(
        contract=contract,
        contract_sha256=sha256_bytes(raw),
        scenario_schema_sha256=sha256_path(scenario_schema_path),
        trace_schema_sha256=sha256_path(trace_schema_path),
    )


def load_scenario_bundle(path: str | Path = DEFAULT_SCENARIO_PATH) -> ScenarioBundle:
    manifest_path = Path(path).resolve()
    manifest, raw = load_json_object(manifest_path)
    required = {
        "scenario_manifest_version",
        "scenario_id",
        "fixture",
        "fixture_sha256",
        "symbol",
        "timeframe_seconds",
        "strategy",
        "assumptions",
        "risk",
        "expected",
    }
    if set(manifest) != required:
        missing = sorted(required - set(manifest))
        unexpected = sorted(set(manifest) - required)
        raise ParityContractError(
            "scenario.json fields differ from v1 contract; "
            f"missing={missing}, unexpected={unexpected}"
        )
    if manifest["scenario_manifest_version"] != SCENARIO_MANIFEST_VERSION:
        raise ParityContractError("scenario.json has an unsupported manifest version")
    if not isinstance(manifest["scenario_id"], str) or not manifest["scenario_id"].strip():
        raise ParityContractError("scenario_id must be a non-empty string")
    fixture_name = manifest["fixture"]
    if (
        not isinstance(fixture_name, str)
        or not fixture_name.strip()
        or Path(fixture_name).name != fixture_name
    ):
        raise ParityContractError("scenario fixture must be a safe filename")
    fixture_path = manifest_path.parent / fixture_name
    fixture_hash = sha256_path(fixture_path)
    if manifest["fixture_sha256"] != fixture_hash:
        raise ParityContractError("scenario fixture_sha256 does not match exact fixture bytes")
    return ScenarioBundle(
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_sha256=sha256_bytes(raw),
        fixture_path=fixture_path,
        fixture_sha256=fixture_hash,
    )


def decimal_string(value: object) -> str:
    """Return one finite, exponent-free, canonical decimal string."""

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ParityContractError("parity numeric values must be int, float, or Decimal")
    if isinstance(value, float) and not isfinite(value):
        raise ParityContractError("parity numeric values must be finite")
    try:
        selected = Decimal(str(value))
    except InvalidOperation as exc:
        raise ParityContractError("parity numeric values must be valid decimals") from exc
    if not selected.is_finite():
        raise ParityContractError("parity numeric values must be finite")
    if selected == 0:
        return "0"
    rendered = format(selected.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def parse_decimal_string(value: object, *, field: str) -> Decimal:
    """Parse a canonical decimal string and reject alternate representations."""

    if not isinstance(value, str):
        raise ParityContractError(f"{field} must be a decimal string")
    try:
        selected = Decimal(value)
    except InvalidOperation as exc:
        raise ParityContractError(f"{field} must be a valid decimal string") from exc
    if not selected.is_finite() or decimal_string(selected) != value:
        raise ParityContractError(f"{field} must be a finite canonical decimal string")
    return selected


def deterministic_json(payload: dict[str, Any]) -> str:
    """Serialize a trace deterministically with a final newline."""

    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


__all__ = [
    "CONTRACT_DIRECTORY",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "DEFAULT_SCENARIO_PATH",
    "REPOSITORY_ROOT",
    "SCENARIO_MANIFEST_VERSION",
    "TRACE_SCHEMA_VERSION",
    "ContractBundle",
    "ParityContractError",
    "ScenarioBundle",
    "decimal_string",
    "deterministic_json",
    "load_contract_bundle",
    "load_json_object",
    "load_scenario_bundle",
    "parse_decimal_string",
    "sha256_bytes",
    "sha256_path",
]
