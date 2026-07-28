from __future__ import annotations

import copy
import json
from hashlib import sha256
from pathlib import Path

import pytest

from trading_bot_lab.lean_validation import (
    DEFAULT_RECORD_PATH,
    INTERPRETATION,
    SCHEMA_PATH,
    CloudValidationError,
    load_cloud_validation_record,
    normalize_cloud_validation_record,
    serialize_cloud_validation_record,
    write_cloud_validation_record,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "lean-workspace" / "Strategies"

EXPECTED_LOG_DIGESTS = {
    "MovingAverageBaseline": {
        "push_log_sha256": "9119062a20ec6d955d23d2b4fae43d04706826e987d3ac7c659664288a7fc612",
        "validation_log_sha256": (
            "40ff843bf3b0f0df44311a070ec36ec1673c1a19febab7ae30c61429def8b8e1"
        ),
    },
    "SkeletonBacktest": {
        "push_log_sha256": "409ca4aec9b277df5e712edede431cb98db3d998c5d6debccd97c1aff3ff8931",
        "validation_log_sha256": (
            "d8d1bb2ac4cb562a562100cc734bf878dddf68734e496af291711fc270a5575c"
        ),
    },
}


@pytest.fixture
def canonical_record() -> dict[str, object]:
    return json.loads(DEFAULT_RECORD_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_canonical_record_is_deterministic_public_and_content_bound(
    canonical_record: dict[str, object],
) -> None:
    normalized = normalize_cloud_validation_record(canonical_record)

    assert normalized == canonical_record
    assert load_cloud_validation_record(DEFAULT_RECORD_PATH) == canonical_record
    assert serialize_cloud_validation_record(canonical_record) == DEFAULT_RECORD_PATH.read_text(
        encoding="utf-8"
    )
    assert normalized["interpretation"] == INTERPRETATION
    assert normalized["validation"] == {
        "cloud_engine_validation": "passed",
        "execution_timing_parity": "pending_identical_data_execution",
        "numerical_accounting_parity": "pending_identical_data_execution",
        "project_synchronization_validation": "passed",
        "source_configuration_validation": "passed",
    }

    record_text = DEFAULT_RECORD_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "account_email",
        "access_token",
        "backtest_id",
        "billing",
        "cloud-id",
        "invoice",
        "organization-id",
        "owner_name",
        "project_id",
        "subscription",
        "try/catch",
        "http://",
        "https://",
        "/home/",
        "C:\\",
    ):
        assert forbidden not in record_text

    by_name = {project["algorithm_name"]: project for project in normalized["projects"]}
    for algorithm_name, project in by_name.items():
        project_root = PROJECTS / algorithm_name
        config = json.loads((project_root / "config.json").read_text(encoding="utf-8"))
        assert set(config) == {"algorithm-language", "description", "parameters"}
        assert ";" not in config["description"]
        assert project["parameters"] == config["parameters"]
        assert project["evidence_sha256"]["source_sha256"] == _sha256(project_root / "main.py")
        assert project["evidence_sha256"]["public_configuration_sha256"] == _sha256(
            project_root / "config.json"
        )
        assert {
            key: project["evidence_sha256"][key] for key in EXPECTED_LOG_DIGESTS[algorithm_name]
        } == EXPECTED_LOG_DIGESTS[algorithm_name]


def test_record_schema_is_closed_and_version_compatible(
    canonical_record: dict[str, object],
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert schema["$id"] == "urn:trading-bot-lab:lean-cloud-validation:v1:record"
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["properties"]["record_type"]["const"] == "lean_cloud_validation"
    assert schema["properties"]["interpretation"]["const"] == INTERPRETATION
    assert schema["properties"]["projects"]["items"]["additionalProperties"] is False
    assert len(schema["$defs"]["lifecycle"]["allOf"]) == 2
    assert len(schema["allOf"]) == 3

    projects_schema = schema["properties"]["projects"]
    required_projects = {
        clause["contains"]["properties"]["project_name"]["const"]
        for clause in projects_schema["allOf"]
    }
    assert required_projects == {
        "Strategies/MovingAverageBaseline",
        "Strategies/SkeletonBacktest",
    }
    project_rules = {
        clause["if"]["properties"]["project_name"]["const"]: clause["then"]["properties"]
        for clause in projects_schema["items"]["allOf"]
    }
    for project in canonical_record["projects"]:
        rule = project_rules[project["project_name"]]
        assert rule["algorithm_name"]["const"] == project["algorithm_name"]
        parameter_ref = rule["parameters"]["$ref"].rsplit("/", maxsplit=1)[1]
        assert schema["$defs"][parameter_ref]["const"] == project["parameters"]
    assert (
        schema["properties"]["parity_evidence"]["properties"]["identical_fixture_executed"]["const"]
        is False
    )
    assert canonical_record["schema_version"] == "1.0.0"
    assert canonical_record["parity_evidence"]["contract_version"] == "1.0.0"


def test_record_schema_declares_portable_numeric_and_timestamp_invariants() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    metrics = schema["$defs"]["metrics"]["properties"]

    assert metrics["starting_equity_usd"]["$ref"].endswith("/positiveDecimalString")
    assert metrics["ending_equity_usd"]["$ref"].endswith("/positiveDecimalString")
    assert metrics["ending_holdings_value_usd"]["$ref"].endswith("/nonNegativeDecimalString")
    assert metrics["simulated_fees_usd"]["$ref"].endswith("/nonNegativeDecimalString")
    assert metrics["maximum_drawdown_ratio"]["$ref"].endswith("/unitIntervalDecimalString")
    timestamp = schema["properties"]["projects"]["items"]["properties"]["execution_timestamp_utc"]
    assert timestamp["pattern"].endswith("Z$")
    assert "normalizer" in timestamp["$comment"]


@pytest.mark.parametrize(
    "field",
    [
        "account_email",
        "owner_name",
        "organization_id",
        "organization_invitation_url",
        "billing_data",
        "invoice_id",
        "subscriptions",
        "node_id",
        "module_license",
        "access_token",
        "authentication_material",
        "project_id",
        "backtest_id",
        "cloud_id",
        "local_id",
        "absolute_path",
        "api_key",
        "private_key",
    ],
)
def test_forbidden_fields_are_rejected(
    canonical_record: dict[str, object],
    field: str,
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate[field] = "not-public"

    with pytest.raises(CloudValidationError, match="forbidden field"):
        normalize_cloud_validation_record(candidate)


@pytest.mark.parametrize(
    "value",
    [
        "owner@example.com",
        "https://example.invalid/private",
        "/home/operator/private.json",
        "loaded from /home/operator/private.json",
        "C:\\Users\\operator\\private.json",
        "loaded from C:\\Users\\operator\\private.json",
        "\\\\server\\share\\private.json",
        "-----BEGIN " + "PRIVATE KEY-----",
        "Bearer not-public",
        "ghp_abcdefghijklmnop",
        "sk-abcdefghijklmnop",
        "xoxb-123456789012-abcdefghij",
        "AKIA" + "ABCDEFGHIJKLMNOP",
    ],
)
def test_secret_path_email_and_url_values_are_rejected(
    canonical_record: dict[str, object],
    value: str,
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate["engine"]["version"] = value

    with pytest.raises(CloudValidationError):
        normalize_cloud_validation_record(candidate)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_nan_and_infinity_are_rejected(
    canonical_record: dict[str, object],
    value: object,
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate["projects"][0]["metrics"]["ending_equity_usd"] = value

    with pytest.raises(CloudValidationError):
        normalize_cloud_validation_record(candidate)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_projects",
        "unexpected_field",
        "bad_version",
        "bad_interpretation",
        "algorithm_mismatch",
        "completion_without_initialization",
        "duplicate_project",
        "missing_project",
        "bad_timestamp",
        "missing_digest",
        "uppercase_digest",
        "changed_parameter",
        "extra_parameter",
        "missing_warning",
    ],
)
def test_malformed_evidence_is_rejected(
    canonical_record: dict[str, object],
    mutation: str,
) -> None:
    candidate = copy.deepcopy(canonical_record)
    if mutation == "missing_projects":
        del candidate["projects"]
    elif mutation == "unexpected_field":
        candidate["engine"]["raw_output"] = "not allowed"
    elif mutation == "bad_version":
        candidate["schema_version"] = "2.0.0"
    elif mutation == "bad_interpretation":
        candidate["interpretation"] = "profitable_strategy"
    elif mutation == "algorithm_mismatch":
        candidate["projects"][0]["algorithm_name"] = "DifferentAlgorithm"
    elif mutation == "completion_without_initialization":
        candidate["projects"][0]["lifecycle"]["initialization"] = "not_observed"
    elif mutation == "duplicate_project":
        candidate["projects"].append(copy.deepcopy(candidate["projects"][0]))
    elif mutation == "missing_project":
        candidate["projects"].pop()
    elif mutation == "bad_timestamp":
        candidate["projects"][0]["execution_timestamp_utc"] = "2026-07-28 04:05:05"
    elif mutation == "missing_digest":
        del candidate["projects"][0]["evidence_sha256"]["push_log_sha256"]
    elif mutation == "uppercase_digest":
        candidate["projects"][0]["evidence_sha256"]["push_log_sha256"] = "A" * 64
    elif mutation == "changed_parameter":
        candidate["projects"][0]["parameters"]["symbol"] = "BTCUSD"
    elif mutation == "extra_parameter":
        candidate["projects"][0]["parameters"]["api-key"] = "not-public"
    else:
        candidate["projects"][0]["warning_categories"] = []

    with pytest.raises(CloudValidationError):
        normalize_cloud_validation_record(candidate)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    selected = tmp_path / "duplicate.json"
    selected.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}\n')

    with pytest.raises(CloudValidationError, match="duplicate key"):
        load_cloud_validation_record(selected)


def test_parameter_and_project_order_do_not_change_serialization(
    canonical_record: dict[str, object],
) -> None:
    reordered = copy.deepcopy(canonical_record)
    reordered["projects"].reverse()
    for project in reordered["projects"]:
        project["parameters"] = dict(reversed(list(project["parameters"].items())))
        project["evidence_sha256"] = dict(reversed(list(project["evidence_sha256"].items())))

    assert serialize_cloud_validation_record(reordered) == serialize_cloud_validation_record(
        canonical_record
    )


def test_warning_categories_are_stable_and_raw_text_is_rejected(
    canonical_record: dict[str, object],
) -> None:
    assert all(
        project["warning_categories"] == ["discouraged_exception_handling"]
        for project in canonical_record["projects"]
    )
    raw_warning = copy.deepcopy(canonical_record)
    raw_warning["projects"][0]["warning_categories"] = [
        "Use of try/catch is discouraged as it can hide errors"
    ]
    with pytest.raises(CloudValidationError, match="raw warning text"):
        normalize_cloud_validation_record(raw_warning)


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.json",
        "..\\escape.json",
        "/tmp/escape.json",
        "C:\\temp\\escape.json",
        "\\\\server\\share\\escape.json",
        ".",
        "record.txt",
    ],
)
def test_output_path_rejects_posix_and_windows_escapes(
    tmp_path: Path,
    canonical_record: dict[str, object],
    filename: str,
) -> None:
    with pytest.raises(CloudValidationError):
        write_cloud_validation_record(
            canonical_record,
            output_directory=tmp_path / "records",
            filename=filename,
        )


def test_atomic_output_is_byte_stable_and_preserves_existing_file_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canonical_record: dict[str, object],
) -> None:
    output_root = tmp_path / "records"
    first = write_cloud_validation_record(
        canonical_record,
        output_directory=output_root,
        filename="first.json",
    )
    second = write_cloud_validation_record(
        canonical_record,
        output_directory=output_root,
        filename="second.json",
    )
    assert first.read_bytes() == second.read_bytes() == DEFAULT_RECORD_PATH.read_bytes()

    first.write_text("preserve-me\n", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("trading_bot_lab.artifacts.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        write_cloud_validation_record(
            canonical_record,
            output_directory=output_root,
            filename="first.json",
        )
    assert first.read_text(encoding="utf-8") == "preserve-me\n"
    assert not [path for path in output_root.iterdir() if path.name.endswith(".tmp")]


@pytest.mark.parametrize("target_location", ["inside", "outside"])
def test_existing_symlink_destination_is_rejected(
    tmp_path: Path,
    canonical_record: dict[str, object],
    target_location: str,
) -> None:
    output_root = tmp_path / "records"
    output_root.mkdir()
    target = (
        output_root / "important.json" if target_location == "inside" else tmp_path / "outside.json"
    )
    target.write_text("preserve-me\n", encoding="utf-8")
    link = output_root / "record.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(CloudValidationError, match="must not be a symlink"):
        write_cloud_validation_record(
            canonical_record,
            output_directory=output_root,
            filename="record.json",
        )
    assert target.read_text(encoding="utf-8") == "preserve-me\n"
    assert link.is_symlink()


def test_parity_classification_cannot_be_promoted_without_authoritative_comparison(
    canonical_record: dict[str, object],
) -> None:
    false_claim = copy.deepcopy(canonical_record)
    false_claim["validation"]["numerical_accounting_parity"] = "passed"
    with pytest.raises(CloudValidationError, match="does not match"):
        normalize_cloud_validation_record(false_claim)

    for provenance in (
        "contract_fixture_not_engine_observation",
        "lean_engine_observation",
    ):
        promotion = copy.deepcopy(canonical_record)
        promotion.pop("validation")
        promotion["parity_evidence"] = {
            "comparison_matched": True,
            "contract_name": "trading_bot_lab_cross_engine_parity",
            "contract_version": "1.0.0",
            "data_scope": "versioned_synthetic_fixture",
            "identical_fixture_executed": True,
            "normalized_trace_provenance": provenance,
            "trace_schema_version": "1.0.0",
        }
        with pytest.raises(CloudValidationError, match="parity still pending"):
            normalize_cloud_validation_record(promotion)


def test_source_configuration_and_project_synchronization_are_independent(
    canonical_record: dict[str, object],
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate.pop("validation")
    candidate["projects"][0]["lifecycle"]["project_synchronization"] = "not_observed"

    validation = normalize_cloud_validation_record(candidate)["validation"]
    assert validation["project_synchronization_validation"] == "not_observed"
    assert validation["source_configuration_validation"] == "passed"


@pytest.mark.parametrize(
    ("lifecycle_field", "validation_field"),
    [
        ("backtest_completion", "cloud_engine_validation"),
        ("project_synchronization", "project_synchronization_validation"),
        ("source_configuration", "source_configuration_validation"),
    ],
)
@pytest.mark.parametrize(
    ("evidence_status", "expected_status"),
    [("failed", "failed"), ("not_observed", "not_observed")],
)
def test_validation_status_is_derived_from_project_evidence(
    canonical_record: dict[str, object],
    lifecycle_field: str,
    validation_field: str,
    evidence_status: str,
    expected_status: str,
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate.pop("validation")
    candidate["projects"][0]["lifecycle"][lifecycle_field] = evidence_status

    assert (
        normalize_cloud_validation_record(candidate)["validation"][validation_field]
        == expected_status
    )


def test_live_deployment_evidence_is_rejected(
    canonical_record: dict[str, object],
) -> None:
    candidate = copy.deepcopy(canonical_record)
    candidate["projects"][0]["lifecycle"]["live_deployment"] = "present"

    with pytest.raises(CloudValidationError, match="cannot be present"):
        normalize_cloud_validation_record(candidate)
