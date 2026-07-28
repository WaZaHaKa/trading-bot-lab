"""Typed normalization for sanitized QuantConnect cloud-validation evidence.

This module deliberately does not parse or serialize raw cloud logs. Operators
select only the public fields admitted by the closed v1 record; the normalizer
then validates, classifies, and serializes those fields deterministically.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath, PureWindowsPath

from trading_bot_lab.artifacts import atomic_write_text
from trading_bot_lab.parity.contract import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    TRACE_SCHEMA_VERSION,
    ParityContractError,
    deterministic_json,
    load_json_object,
    parse_decimal_string,
)

SCHEMA_VERSION = "1.0.0"
RECORD_TYPE = "lean_cloud_validation"
INTERPRETATION = "validation_observations_only_not_strategy_quality"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIRECTORY = REPOSITORY_ROOT / "contracts" / "lean-cloud-validation" / "v1"
DEFAULT_RECORD_PATH = CONTRACT_DIRECTORY / "2026-07-28.json"
SCHEMA_PATH = CONTRACT_DIRECTORY / "record.schema.json"

_PROJECT_COMPONENT = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_ENGINE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){3,}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
_EMBEDDED_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9._~-])/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]+")
_EMBEDDED_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\s]+")
_EMBEDDED_UNC_PATH = re.compile(r"(?<!\\)\\\\[A-Za-z0-9._$-]+\\[^\s]+")
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "account",
        "accounts",
        "auth",
        "authentication",
        "backtestid",
        "billing",
        "cloudid",
        "credential",
        "credentials",
        "email",
        "emails",
        "invitation",
        "invitations",
        "invoice",
        "invoices",
        "key",
        "keys",
        "license",
        "licenses",
        "localid",
        "module",
        "modules",
        "node",
        "nodes",
        "organization",
        "organizations",
        "owner",
        "owners",
        "password",
        "passwords",
        "path",
        "paths",
        "private",
        "projectid",
        "secret",
        "secrets",
        "subscription",
        "subscriptions",
        "token",
        "tokens",
        "url",
        "urls",
    }
)

_PUBLIC_PARAMETERS: dict[str, dict[str, str]] = {
    "Strategies/MovingAverageBaseline": {
        "end-date": "2021-01-01",
        "fast-period": "20",
        "fee-bps": "1.0",
        "initial-cash": "100000",
        "max-daily-loss": "0.02",
        "max-drawdown": "0.05",
        "max-position-weight": "0.10",
        "max-total-exposure": "0.30",
        "minimum-fee": "1.0",
        "slippage-bps": "2.0",
        "slow-period": "50",
        "start-date": "2020-01-01",
        "symbol": "SPY",
        "target-weight": "0.10",
        "warmup-bars": "50",
    },
    "Strategies/SkeletonBacktest": {
        "end-date": "2023-03-31",
        "initial-cash": "100000",
        "maximum-allocation": "0.05",
        "start-date": "2023-01-01",
    },
}


class CloudValidationError(ValueError):
    """Raised when sanitized cloud evidence violates the public contract."""


class EvidenceStatus(StrEnum):
    """Observed outcome for one bounded cloud-validation stage."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_OBSERVED = "not_observed"


class LiveDeploymentStatus(StrEnum):
    """Whether a live deployment was observed for a backtest-only project."""

    ABSENT = "absent"
    PRESENT = "present"
    NOT_OBSERVED = "not_observed"


class ValidationStatus(StrEnum):
    """Derived validation and parity classifications."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_OBSERVED = "not_observed"
    PENDING_IDENTICAL_DATA = "pending_identical_data_execution"


class WarningCategory(StrEnum):
    """Stable allowlisted warning categories; raw warning text is prohibited."""

    DISCOURAGED_EXCEPTION_HANDLING = "discouraged_exception_handling"


@dataclass(frozen=True)
class EngineIdentity:
    name: str
    version: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True)
class EvidenceDigests:
    source_sha256: str
    public_configuration_sha256: str
    push_log_sha256: str
    validation_log_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "public_configuration_sha256": self.public_configuration_sha256,
            "push_log_sha256": self.push_log_sha256,
            "source_sha256": self.source_sha256,
            "validation_log_sha256": self.validation_log_sha256,
        }


@dataclass(frozen=True)
class ProjectLifecycle:
    project_synchronization: EvidenceStatus
    source_configuration: EvidenceStatus
    compilation: EvidenceStatus
    initialization: EvidenceStatus
    backtest_completion: EvidenceStatus
    live_deployment: LiveDeploymentStatus

    def as_dict(self) -> dict[str, str]:
        return {
            "backtest_completion": self.backtest_completion.value,
            "compilation": self.compilation.value,
            "initialization": self.initialization.value,
            "live_deployment": self.live_deployment.value,
            "project_synchronization": self.project_synchronization.value,
            "source_configuration": self.source_configuration.value,
        }


@dataclass(frozen=True)
class ValidationMetrics:
    starting_equity_usd: str
    ending_equity_usd: str
    ending_holdings_value_usd: str
    order_count: int
    simulated_fees_usd: str
    maximum_drawdown_ratio: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ending_equity_usd": self.ending_equity_usd,
            "ending_holdings_value_usd": self.ending_holdings_value_usd,
            "maximum_drawdown_ratio": self.maximum_drawdown_ratio,
            "order_count": self.order_count,
            "simulated_fees_usd": self.simulated_fees_usd,
            "starting_equity_usd": self.starting_equity_usd,
        }


@dataclass(frozen=True)
class ProjectObservation:
    project_name: str
    algorithm_name: str
    execution_timestamp_utc: str
    parameters: tuple[tuple[str, str], ...]
    evidence_sha256: EvidenceDigests
    lifecycle: ProjectLifecycle
    warning_categories: tuple[WarningCategory, ...]
    metrics: ValidationMetrics

    def as_dict(self) -> dict[str, object]:
        return {
            "algorithm_name": self.algorithm_name,
            "evidence_sha256": self.evidence_sha256.as_dict(),
            "execution_timestamp_utc": self.execution_timestamp_utc,
            "lifecycle": self.lifecycle.as_dict(),
            "metrics": self.metrics.as_dict(),
            "parameters": dict(self.parameters),
            "project_name": self.project_name,
            "warning_categories": [category.value for category in self.warning_categories],
        }


@dataclass(frozen=True)
class ParityEvidence:
    """Evidence that these runs did not execute the committed parity fixture."""

    def as_dict(self) -> dict[str, object]:
        return {
            "comparison_matched": None,
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "data_scope": "quantconnect_spy_cloud_data",
            "identical_fixture_executed": False,
            "normalized_trace_provenance": None,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class CloudValidationRecord:
    execution_date_utc: str
    engine: EngineIdentity
    projects: tuple[ProjectObservation, ...]
    parity_evidence: ParityEvidence

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine.as_dict(),
            "execution_date_utc": self.execution_date_utc,
            "interpretation": INTERPRETATION,
            "parity_evidence": self.parity_evidence.as_dict(),
            "projects": [project.as_dict() for project in self.projects],
            "record_type": RECORD_TYPE,
            "schema_version": SCHEMA_VERSION,
            "validation": _classify(self.projects),
        }


def normalize_cloud_validation_record(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate sanitized evidence and return one canonical public record."""

    if not isinstance(payload, Mapping):
        raise CloudValidationError("cloud validation evidence must be an object")
    _scan_untrusted_value(payload, "record")
    _require_fields(
        payload,
        {
            "engine",
            "execution_date_utc",
            "interpretation",
            "parity_evidence",
            "projects",
            "record_type",
            "schema_version",
        },
        optional={"validation"},
        path="record",
    )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise CloudValidationError("record.schema_version is unsupported")
    if payload["record_type"] != RECORD_TYPE:
        raise CloudValidationError("record.record_type is unsupported")
    if payload["interpretation"] != INTERPRETATION:
        raise CloudValidationError("record.interpretation is unsupported")

    execution_date = _parse_utc_date(payload["execution_date_utc"])
    engine = _parse_engine(payload["engine"])
    projects = _parse_projects(payload["projects"], execution_date)
    parity_evidence = _parse_parity_evidence(payload["parity_evidence"])
    record = CloudValidationRecord(
        execution_date_utc=execution_date,
        engine=engine,
        projects=projects,
        parity_evidence=parity_evidence,
    )
    normalized = record.as_dict()
    if "validation" in payload:
        supplied = _mapping(payload["validation"], "record.validation")
        if dict(supplied) != normalized["validation"]:
            raise CloudValidationError(
                "record.validation does not match the classification derived from evidence"
            )
    return normalized


def load_cloud_validation_record(path: str | Path) -> dict[str, object]:
    """Load strict JSON and normalize it without duplicate or non-finite values."""

    try:
        payload, _ = load_json_object(path)
    except (OSError, ParityContractError) as exc:
        raise CloudValidationError(str(exc)) from exc
    return normalize_cloud_validation_record(payload)


def serialize_cloud_validation_record(payload: Mapping[str, object]) -> str:
    """Return stable, sorted UTF-8 JSON with a final newline."""

    return deterministic_json(normalize_cloud_validation_record(payload))


def write_cloud_validation_record(
    payload: Mapping[str, object],
    *,
    output_directory: str | Path,
    filename: str,
) -> Path:
    """Atomically write a canonical record beneath one explicit output root."""

    safe_name = _safe_output_filename(filename)
    root = Path(output_directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / safe_name
    if destination.is_symlink():
        raise CloudValidationError("output destination must not be a symlink")
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise CloudValidationError("output path escapes the selected output directory") from exc
    return atomic_write_text(destination, serialize_cloud_validation_record(payload))


def _parse_engine(value: object) -> EngineIdentity:
    engine = _mapping(value, "record.engine")
    _require_fields(engine, {"name", "version"}, path="record.engine")
    name = _string(engine["name"], "record.engine.name")
    version = _string(engine["version"], "record.engine.version")
    if name != "QuantConnect LEAN":
        raise CloudValidationError("record.engine.name must be QuantConnect LEAN")
    if not _ENGINE_VERSION.fullmatch(version):
        raise CloudValidationError("record.engine.version must be a numeric LEAN version")
    return EngineIdentity(name=name, version=version)


def _parse_projects(value: object, execution_date: str) -> tuple[ProjectObservation, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CloudValidationError("record.projects must be an array")
    projects = tuple(
        _parse_project(raw, index=index, execution_date=execution_date)
        for index, raw in enumerate(value)
    )
    names = [project.project_name for project in projects]
    if len(names) != len(set(names)):
        raise CloudValidationError("record.projects contains a duplicate project_name")
    if set(names) != set(_PUBLIC_PARAMETERS):
        raise CloudValidationError("record.projects must contain both canonical cloud projects")
    return tuple(sorted(projects, key=lambda project: project.project_name))


def _parse_project(value: object, *, index: int, execution_date: str) -> ProjectObservation:
    path = f"record.projects[{index}]"
    project = _mapping(value, path)
    _require_fields(
        project,
        {
            "algorithm_name",
            "evidence_sha256",
            "execution_timestamp_utc",
            "lifecycle",
            "metrics",
            "parameters",
            "project_name",
            "warning_categories",
        },
        path=path,
    )
    project_name = _safe_project_name(project["project_name"], f"{path}.project_name")
    if project_name not in _PUBLIC_PARAMETERS:
        raise CloudValidationError(f"{path}.project_name is not a canonical cloud project")
    algorithm_name = _string(project["algorithm_name"], f"{path}.algorithm_name")
    if not _PROJECT_COMPONENT.fullmatch(algorithm_name):
        raise CloudValidationError(f"{path}.algorithm_name is not a safe class name")
    if project_name.rsplit("/", maxsplit=1)[1] != algorithm_name:
        raise CloudValidationError(f"{path}.algorithm_name must match the project directory")
    timestamp = _parse_utc_timestamp(
        project["execution_timestamp_utc"],
        f"{path}.execution_timestamp_utc",
    )
    if not timestamp.startswith(execution_date):
        raise CloudValidationError(f"{path}.execution_timestamp_utc differs from record date")
    parameters = _parse_parameters(
        project["parameters"],
        f"{path}.parameters",
        expected=_PUBLIC_PARAMETERS[project_name],
    )
    return ProjectObservation(
        project_name=project_name,
        algorithm_name=algorithm_name,
        execution_timestamp_utc=timestamp,
        parameters=parameters,
        evidence_sha256=_parse_evidence_digests(
            project["evidence_sha256"], f"{path}.evidence_sha256"
        ),
        lifecycle=_parse_lifecycle(project["lifecycle"], f"{path}.lifecycle"),
        warning_categories=_parse_warnings(
            project["warning_categories"], f"{path}.warning_categories"
        ),
        metrics=_parse_metrics(project["metrics"], f"{path}.metrics"),
    )


def _parse_evidence_digests(value: object, path: str) -> EvidenceDigests:
    digests = _mapping(value, path)
    fields = {
        "public_configuration_sha256",
        "push_log_sha256",
        "source_sha256",
        "validation_log_sha256",
    }
    _require_fields(digests, fields, path=path)
    parsed = {field: _string(digests[field], f"{path}.{field}") for field in fields}
    for field, digest in parsed.items():
        if not _SHA256.fullmatch(digest):
            raise CloudValidationError(f"{path}.{field} must be a lowercase SHA-256 digest")
    return EvidenceDigests(
        source_sha256=parsed["source_sha256"],
        public_configuration_sha256=parsed["public_configuration_sha256"],
        push_log_sha256=parsed["push_log_sha256"],
        validation_log_sha256=parsed["validation_log_sha256"],
    )


def _parse_lifecycle(value: object, path: str) -> ProjectLifecycle:
    lifecycle = _mapping(value, path)
    _require_fields(
        lifecycle,
        {
            "backtest_completion",
            "compilation",
            "initialization",
            "live_deployment",
            "project_synchronization",
            "source_configuration",
        },
        path=path,
    )
    try:
        parsed = ProjectLifecycle(
            project_synchronization=EvidenceStatus(lifecycle["project_synchronization"]),
            source_configuration=EvidenceStatus(lifecycle["source_configuration"]),
            compilation=EvidenceStatus(lifecycle["compilation"]),
            initialization=EvidenceStatus(lifecycle["initialization"]),
            backtest_completion=EvidenceStatus(lifecycle["backtest_completion"]),
            live_deployment=LiveDeploymentStatus(lifecycle["live_deployment"]),
        )
    except (TypeError, ValueError) as exc:
        raise CloudValidationError(f"{path} contains an unsupported status") from exc
    if parsed.live_deployment is LiveDeploymentStatus.PRESENT:
        raise CloudValidationError(f"{path}.live_deployment cannot be present")
    if (
        parsed.backtest_completion is EvidenceStatus.PASSED
        and parsed.initialization is not EvidenceStatus.PASSED
    ):
        raise CloudValidationError(f"{path} cannot complete without successful initialization")
    if (
        parsed.initialization is EvidenceStatus.PASSED
        and parsed.compilation is not EvidenceStatus.PASSED
    ):
        raise CloudValidationError(f"{path} cannot initialize without successful compilation")
    return parsed


def _parse_parameters(
    value: object,
    path: str,
    *,
    expected: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    parameters = _mapping(value, path)
    if dict(parameters) != dict(expected):
        raise CloudValidationError(f"{path} must match the canonical public configuration")
    return tuple(sorted((key, str(raw)) for key, raw in parameters.items()))


def _parse_warnings(value: object, path: str) -> tuple[WarningCategory, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CloudValidationError(f"{path} must be an array")
    try:
        warnings = tuple(WarningCategory(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise CloudValidationError(f"{path} contains unsupported or raw warning text") from exc
    expected = (WarningCategory.DISCOURAGED_EXCEPTION_HANDLING,)
    if warnings != expected:
        raise CloudValidationError(f"{path} must contain the canonical stable warning category")
    return warnings


def _parse_metrics(value: object, path: str) -> ValidationMetrics:
    metrics = _mapping(value, path)
    _require_fields(
        metrics,
        {
            "ending_equity_usd",
            "ending_holdings_value_usd",
            "maximum_drawdown_ratio",
            "order_count",
            "simulated_fees_usd",
            "starting_equity_usd",
        },
        path=path,
    )
    order_count = metrics["order_count"]
    if isinstance(order_count, bool) or not isinstance(order_count, int) or order_count < 0:
        raise CloudValidationError(f"{path}.order_count must be a non-negative integer")
    starting = _decimal(metrics["starting_equity_usd"], f"{path}.starting_equity_usd")
    ending = _decimal(metrics["ending_equity_usd"], f"{path}.ending_equity_usd")
    holdings = _decimal(metrics["ending_holdings_value_usd"], f"{path}.ending_holdings_value_usd")
    fees = _decimal(metrics["simulated_fees_usd"], f"{path}.simulated_fees_usd")
    drawdown = _decimal(metrics["maximum_drawdown_ratio"], f"{path}.maximum_drawdown_ratio")
    if starting <= 0 or ending <= 0:
        raise CloudValidationError(f"{path} equity values must be positive")
    if holdings < 0 or fees < 0:
        raise CloudValidationError(f"{path} holdings and fees must be non-negative")
    if drawdown < 0 or drawdown > 1:
        raise CloudValidationError(f"{path}.maximum_drawdown_ratio must be between zero and one")
    return ValidationMetrics(
        starting_equity_usd=str(metrics["starting_equity_usd"]),
        ending_equity_usd=str(metrics["ending_equity_usd"]),
        ending_holdings_value_usd=str(metrics["ending_holdings_value_usd"]),
        order_count=order_count,
        simulated_fees_usd=str(metrics["simulated_fees_usd"]),
        maximum_drawdown_ratio=str(metrics["maximum_drawdown_ratio"]),
    )


def _parse_parity_evidence(value: object) -> ParityEvidence:
    path = "record.parity_evidence"
    evidence = _mapping(value, path)
    expected: dict[str, object] = {
        "comparison_matched": None,
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "data_scope": "quantconnect_spy_cloud_data",
        "identical_fixture_executed": False,
        "normalized_trace_provenance": None,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
    }
    _require_fields(evidence, set(expected), path=path)
    if dict(evidence) != expected:
        raise CloudValidationError(
            f"{path} must describe QuantConnect SPY data with parity still pending"
        )
    return ParityEvidence()


def _classify(projects: tuple[ProjectObservation, ...]) -> dict[str, str]:
    cloud_stages = tuple(
        status
        for project in projects
        for status in (
            project.lifecycle.compilation,
            project.lifecycle.initialization,
            project.lifecycle.backtest_completion,
        )
    )
    return {
        "cloud_engine_validation": _aggregate(cloud_stages).value,
        "execution_timing_parity": ValidationStatus.PENDING_IDENTICAL_DATA.value,
        "numerical_accounting_parity": ValidationStatus.PENDING_IDENTICAL_DATA.value,
        "project_synchronization_validation": _aggregate(
            tuple(project.lifecycle.project_synchronization for project in projects)
        ).value,
        "source_configuration_validation": _aggregate(
            tuple(project.lifecycle.source_configuration for project in projects)
        ).value,
    }


def _aggregate(statuses: tuple[EvidenceStatus, ...]) -> ValidationStatus:
    if all(status is EvidenceStatus.PASSED for status in statuses):
        return ValidationStatus.PASSED
    if any(status is EvidenceStatus.FAILED for status in statuses):
        return ValidationStatus.FAILED
    return ValidationStatus.NOT_OBSERVED


def _scan_untrusted_value(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise CloudValidationError(f"{path} contains a non-string field name")
            tokens = _field_tokens(raw_key)
            if _FORBIDDEN_KEY_TOKENS.intersection(tokens):
                raise CloudValidationError(f"{path} contains forbidden field {raw_key!r}")
            _scan_untrusted_value(child, f"{path}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _scan_untrusted_value(child, f"{path}[{index}]")
        return
    if isinstance(value, float) and not isfinite(value):
        raise CloudValidationError(f"{path} contains a non-finite number")
    if isinstance(value, str):
        if any(ord(character) < 32 for character in value):
            raise CloudValidationError(f"{path} contains control characters")
        if _EMAIL.search(value):
            raise CloudValidationError(f"{path} contains an email address")
        if _SECRET_VALUE.search(value):
            raise CloudValidationError(f"{path} contains secret-like authentication material")
        if "//" in value and "://" in value:
            raise CloudValidationError(f"{path} contains URL text")
        if value.startswith("-----BEGIN ") or value.lower().startswith("bearer "):
            raise CloudValidationError(f"{path} contains authentication material")
        if (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
            or _EMBEDDED_POSIX_PATH.search(value)
            or _EMBEDDED_WINDOWS_PATH.search(value)
            or _EMBEDDED_UNC_PATH.search(value)
        ):
            raise CloudValidationError(f"{path} contains an absolute machine path")


def _field_tokens(value: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", separated).strip("-").lower()
    tokens = set(normalized.split("-")) if normalized else set()
    compact = normalized.replace("-", "")
    if compact:
        tokens.add(compact)
    return tokens


def _safe_project_name(value: object, path: str) -> str:
    selected = _string(value, path)
    if "\\" in selected:
        raise CloudValidationError(f"{path} must use a safe POSIX project name")
    project = PurePosixPath(selected)
    if project.is_absolute() or project.parts[:1] != ("Strategies",) or len(project.parts) != 2:
        raise CloudValidationError(f"{path} must be Strategies/<ProjectName>")
    if project.parts[1] in {".", ".."} or not _PROJECT_COMPONENT.fullmatch(project.parts[1]):
        raise CloudValidationError(f"{path} contains an unsafe project component")
    return project.as_posix()


def _safe_output_filename(value: object) -> str:
    selected = _string(value, "output filename")
    posix = PurePosixPath(selected)
    windows = PureWindowsPath(selected)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or posix.name != selected
        or windows.name != selected
        or selected in {".", ".."}
    ):
        raise CloudValidationError("output filename must be one safe basename")
    if not selected.endswith(".json"):
        raise CloudValidationError("output filename must end with .json")
    return selected


def _parse_utc_date(value: object) -> str:
    selected = _string(value, "record.execution_date_utc")
    try:
        parsed = date.fromisoformat(selected)
    except ValueError as exc:
        raise CloudValidationError("record.execution_date_utc must use YYYY-MM-DD") from exc
    if parsed.isoformat() != selected:
        raise CloudValidationError("record.execution_date_utc must use canonical YYYY-MM-DD")
    return selected


def _parse_utc_timestamp(value: object, path: str) -> str:
    selected = _string(value, path)
    try:
        parsed = datetime.strptime(selected, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise CloudValidationError(f"{path} must use canonical UTC seconds ending in Z") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != selected:
        raise CloudValidationError(f"{path} must use canonical UTC seconds ending in Z")
    return selected


def _decimal(value: object, path: str) -> Decimal:
    try:
        return parse_decimal_string(value, field=path)
    except ParityContractError as exc:
        raise CloudValidationError(str(exc)) from exc


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CloudValidationError(f"{path} must be an object")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CloudValidationError(f"{path} must be a non-empty string")
    return value


def _require_fields(
    values: Mapping[str, object],
    required: set[str],
    *,
    path: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    actual = set(values)
    if not required.issubset(actual) or not actual.issubset(required | optional):
        missing = sorted(required - actual)
        unexpected = sorted(actual - required - optional)
        raise CloudValidationError(
            f"{path} fields differ from schema; missing={missing}, unexpected={unexpected}"
        )


__all__ = [
    "CONTRACT_DIRECTORY",
    "DEFAULT_RECORD_PATH",
    "INTERPRETATION",
    "RECORD_TYPE",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "CloudValidationError",
    "load_cloud_validation_record",
    "normalize_cloud_validation_record",
    "serialize_cloud_validation_record",
    "write_cloud_validation_record",
]
