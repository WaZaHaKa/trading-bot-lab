from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot_lab.parity import (
    DEFAULT_SCENARIO_PATH,
    ParityMismatchError,
    ParityValidationError,
    build_local_parity_trace,
    compare_parity_files,
    compare_parity_traces,
    write_local_parity_trace,
)
from trading_bot_lab.parity.contract import (
    CONTRACT_DIRECTORY,
    decimal_string,
    load_scenario_bundle,
    sha256_path,
)


@pytest.fixture(scope="module")
def local_trace() -> dict[str, object]:
    return build_local_parity_trace()


def contract_candidate(local_trace: dict[str, object]) -> dict[str, object]:
    """Create a labelled test fixture, never an asserted LEAN observation."""

    candidate = copy.deepcopy(local_trace)
    candidate["provenance"] = "contract_fixture_not_engine_observation"
    candidate["engine"] = {
        "name": "lean_contract_fixture",
        "version": "not_observed_from_lean",
    }
    return candidate


def test_scenario_is_weekday_only_and_exact_byte_bound() -> None:
    scenario = load_scenario_bundle()
    rows = scenario.fixture_path.read_text(encoding="utf-8").splitlines()[1:]

    assert rows
    assert all(date.fromisoformat(row.split(",", maxsplit=1)[0]).weekday() < 5 for row in rows)
    assert scenario.manifest["fixture_sha256"] == sha256_path(scenario.fixture_path)
    assert scenario.manifest["strategy"] == {
        "fast_window": 2,
        "name": "moving_average",
        "slow_window": 3,
        "target_weight": "0.1",
    }
    assert scenario.manifest["assumptions"]["quantity_precision"] == 0
    assert scenario.manifest["assumptions"]["fee_model"] == "notional_bps"
    assert scenario.manifest["assumptions"]["slippage_model"] == "adverse_bps"


def test_actual_local_oracle_output_covers_costs_risk_and_final_bar(
    local_trace: dict[str, object],
) -> None:
    bars = local_trace["bars"]
    intents = local_trace["order_intents"]
    fills = local_trace["fills"]
    trades = local_trace["trades"]
    final = local_trace["final_bar"]
    summary = local_trace["summary"]

    assert local_trace["provenance"] == "local_python_oracle_observation"
    assert len(bars) == 8
    assert [fill["side"] for fill in fills] == ["buy", "sell"]
    assert len(intents) == len(fills) == len(trades) == 2
    assert Decimal(summary["total_fees_paid"]) > 0
    assert Decimal(summary["estimated_slippage_cost"]) > 0
    assert summary["rejected_order_count"] == 0
    assert all(decision["status"] == "approved" for decision in local_trace["risk_decisions"])
    assert final == {
        "creates_fill": False,
        "creates_intent": False,
        "pending_signal_unfilled": True,
        "target_weight": "0.1",
        "timestamp": bars[-1]["timestamp"],
    }
    assert not any(intent["signal_timestamp"] == final["timestamp"] for intent in intents)
    assert not any(trade["signal_timestamp"] == final["timestamp"] for trade in trades)


def test_exact_contract_fixture_matches(local_trace: dict[str, object]) -> None:
    candidate = contract_candidate(local_trace)

    comparison = compare_parity_traces(local_trace, candidate)

    assert comparison.matched
    assert comparison.candidate_provenance == "contract_fixture_not_engine_observation"
    assert comparison.bars_compared == 8
    assert comparison.fills_compared == 2


def test_documented_field_tolerances_are_allowed(local_trace: dict[str, object]) -> None:
    candidate = contract_candidate(local_trace)
    candidate["bars"][4]["equity"] = decimal_string(
        Decimal(candidate["bars"][4]["equity"]) + Decimal("0.009")
    )
    candidate["bars"][4]["close"] = decimal_string(
        Decimal(candidate["bars"][4]["close"]) + Decimal("0.000000005")
    )
    candidate["bars"][4]["exposure_pct"] = decimal_string(
        Decimal(candidate["bars"][4]["exposure_pct"]) + Decimal("0.00000009")
    )

    assert compare_parity_traces(local_trace, candidate).matched


@pytest.mark.parametrize(
    ("section", "index", "field", "replacement"),
    [
        ("fills", 0, "side", "sell"),
        ("bars", 4, "equity", "999999"),
        ("risk_decisions", 0, "status", "rejected"),
    ],
)
def test_unexplained_divergence_fails(
    local_trace: dict[str, object],
    section: str,
    index: int,
    field: str,
    replacement: object,
) -> None:
    candidate = contract_candidate(local_trace)
    candidate[section][index][field] = replacement

    with pytest.raises((ParityMismatchError, ParityValidationError)):
        compare_parity_traces(local_trace, candidate)


@pytest.mark.parametrize(
    "mutation",
    [
        "bad_provenance",
        "bad_contract_hash",
        "bad_fixture_hash",
        "bad_schema_version",
    ],
)
def test_bad_provenance_hash_or_version_is_rejected(
    local_trace: dict[str, object],
    mutation: str,
) -> None:
    candidate = contract_candidate(local_trace)
    if mutation == "bad_provenance":
        candidate["provenance"] = "copied_local_output_claimed_as_lean"
    elif mutation == "bad_contract_hash":
        candidate["contract"]["contract_sha256"] = "0" * 64
    elif mutation == "bad_fixture_hash":
        candidate["scenario"]["fixture_sha256"] = "0" * 64
    else:
        candidate["schema_version"] = "2.0.0"

    with pytest.raises(ParityValidationError):
        compare_parity_traces(local_trace, candidate)


def test_missing_fields_and_changed_final_bar_semantics_fail(
    local_trace: dict[str, object],
) -> None:
    missing = contract_candidate(local_trace)
    del missing["fills"][0]["fee"]
    with pytest.raises(ParityValidationError, match="missing=.*fee"):
        compare_parity_traces(local_trace, missing)

    final_fill = contract_candidate(local_trace)
    final_fill["final_bar"]["creates_fill"] = True
    final_fill["final_bar"]["pending_signal_unfilled"] = False
    with pytest.raises(ParityValidationError, match="final-bar semantics"):
        compare_parity_traces(local_trace, final_fill)

    bad_reference = contract_candidate(local_trace)
    bad_reference["fills"][0]["intent_index"] = "0"
    with pytest.raises(ParityValidationError, match="intent_index must be an integer"):
        compare_parity_traces(local_trace, bad_reference)


def test_export_is_byte_stable_and_file_comparator_is_offline(
    tmp_path: Path,
    local_trace: dict[str, object],
) -> None:
    first = write_local_parity_trace(tmp_path / "local-first.json")
    second = write_local_parity_trace(tmp_path / "local-second.json")
    candidate_path = tmp_path / "lean-contract-fixture.json"
    candidate_path.write_text(
        json.dumps(contract_candidate(local_trace), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert first.read_bytes() == second.read_bytes()
    assert compare_parity_files(first, candidate_path).matched


def test_contract_files_and_scenario_are_public_safe() -> None:
    trace_schema = json.loads(
        (CONTRACT_DIRECTORY / "trace.schema.json").read_text(encoding="utf-8")
    )
    scenario_schema = json.loads(
        (CONTRACT_DIRECTORY / "scenario.schema.json").read_text(encoding="utf-8")
    )
    scenario_text = DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8").lower()

    assert trace_schema["properties"]["provenance"]
    assert scenario_schema["properties"]["fixture_sha256"]
    assert "api_key" not in scenario_text
    assert "token" not in scenario_text
    assert "account" not in scenario_text
