from __future__ import annotations

import json
import re
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from trading_bot_lab.parity.compare import COMPARISON_DIMENSIONS

ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = ROOT / "contracts" / "lean-local-parity" / "v1" / "2026-07-28.json"
SCHEMA_PATH = ROOT / "contracts" / "lean-local-parity" / "v1" / "record.schema.json"
PROJECT = ROOT / "lean-workspace" / "Strategies" / "ParityFixtureV1"
FIXTURE = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
SCENARIO = ROOT / "tests" / "fixtures" / "parity" / "v1" / "scenario.json"
CONTRACT = ROOT / "contracts" / "parity" / "v1" / "contract.json"
HISTORICAL_RECORD_SHA256 = "5832d6948bec3e4e672227200bf9e03484e5515afa2c3a14e07682e3200cf6f5"
HISTORICAL_ALGORITHM_SOURCE_SHA256 = (
    "55d012866ca83622e433b6b298bbac9f0f7cf1c7254679c1bd653b7b23d1ac6f"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_float(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_float(nested)


def test_local_parity_record_is_deterministic_public_and_content_bound() -> None:
    text = RECORD_PATH.read_text(encoding="utf-8")
    record = json.loads(text)

    assert _sha256(RECORD_PATH) == HISTORICAL_RECORD_SHA256
    assert text == json.dumps(record, allow_nan=False, indent=2, sort_keys=True) + "\n"
    _assert_no_float(record)
    assert record["schema_version"] == "1.0.0"
    assert record["record_type"] == "lean_local_parity_validation"
    assert record["classifications"] == {
        "execution_timing_parity": "passed",
        "numerical_accounting_parity": "failed",
        "overall_status": "genuine_local_lean_parity_failed",
    }
    assert record["runtime"]["pull_count"] == 1
    assert record["runtime"]["execution_count"] == 5
    assert record["runtime"]["platform"] == "linux/amd64"
    assert set(record["comparison"]["dimensions"]) == set(COMPARISON_DIMENSIONS)
    assert sum(status == "passed" for status in record["comparison"]["dimensions"].values()) == 15
    assert record["comparison"]["dimensions"]["rejection_and_halt_state"] == "failed"
    assert record["comparison"]["matched"] is False

    evidence = record["evidence_sha256"]
    assert all(SHA256_PATTERN.fullmatch(value) for value in evidence.values())
    assert evidence["algorithm_source_sha256"] == HISTORICAL_ALGORITHM_SOURCE_SHA256
    assert evidence["public_configuration_sha256"] == _sha256(PROJECT / "config.json")
    assert evidence["fixture_sha256"] == _sha256(FIXTURE)
    assert evidence["scenario_manifest_sha256"] == _sha256(SCENARIO)
    assert evidence["contract_sha256"] == _sha256(CONTRACT)

    lowered = text.casefold()
    for forbidden in (
        "account_email",
        "access_token",
        "backtest_id",
        "billing",
        "cloud-id",
        "invoice",
        "local-id",
        "organization",
        "owner_name",
        "project_id",
        "subscription",
        "http://",
        "https://",
        "/home/",
        "c:\\\\",
    ):
        assert forbidden not in lowered


def test_failed_dimension_and_divergence_are_truthful_and_bounded() -> None:
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    divergence = record["comparison"]["divergences"]

    assert len(divergence) == 1
    assert divergence[0]["dimension"] == "rejection_and_halt_state"
    assert divergence[0]["cause_category"] == "current_row_close_risk_snapshot"
    assert divergence[0]["risk_outcome"] == "approved_risk_reducing_exit_in_both_engines"
    assert {item["field"] for item in divergence[0]["observations"]} == {
        "daily_loss_pct",
        "drawdown_pct",
        "order_weight",
    }
    for item in divergence[0]["observations"]:
        expected = Decimal(item["expected"])
        observed = Decimal(item["observed"])
        tolerance = Decimal(item["tolerance"])
        assert expected.is_finite() and observed.is_finite() and tolerance.is_finite()
        assert abs(expected - observed) > tolerance


def test_local_parity_schema_is_closed_and_versioned() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "urn:trading-bot-lab:lean-local-parity-validation:v1:record"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["properties"]["record_type"]["const"] == "lean_local_parity_validation"
    assert schema["properties"]["classifications"]["additionalProperties"] is False
    assert schema["properties"]["comparison"]["additionalProperties"] is False
    assert (
        schema["properties"]["comparison"]["properties"]["dimensions"]["additionalProperties"]
        is False
    )
    assert schema["properties"]["runtime"]["additionalProperties"] is False
    assert schema["properties"]["safety"]["additionalProperties"] is False
    assert set(schema["required"]) == set(record)
    assert set(schema["properties"]["comparison"]["properties"]["dimensions"]["required"]) == set(
        COMPARISON_DIMENSIONS
    )
