from __future__ import annotations

import copy
import json
import re
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from trading_bot_lab.parity.compare import COMPARISON_DIMENSIONS
from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward import contract as walk_forward_contract
from trading_bot_lab.walk_forward.contract import (
    FOLD_IDS,
    FOLDS,
    PERMITTED_RESULT_FIELDS,
    PROHIBITED_IDENTITY_FIELDS,
    PROTOCOL_PATH,
    ProtocolBundle,
    WalkForwardContractError,
    fold_by_id,
    load_protocol_bundle,
    project_source_sha256,
    public_configuration_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FOLDS = (
    ("spy-2021", "2021-01-01", "2021-12-31"),
    ("spy-2022", "2022-01-01", "2022-12-31"),
    ("spy-2023", "2023-01-01", "2023-12-31"),
    ("spy-2024", "2024-01-01", "2024-12-31"),
    ("spy-2025", "2025-01-01", "2025-12-31"),
)
EXPECTED_PARITY_DIMENSIONS = (
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
IMMUTABLE_FILE_SHA256 = {
    "lean-workspace/Strategies/MovingAverageBaseline/main.py": (
        "d30d89107580a65b5eccebd0baa6d3f135e4b49cf3b456c6d2954dda9e660df7"
    ),
    "lean-workspace/Strategies/MovingAverageBaseline/config.json": (
        "14cf812e248fde5df504438ed775dfbccb3e979504144cf1b0e4def22b0f68e3"
    ),
    "lean-workspace/Strategies/ParityFixtureV1/main.py": (
        "9269c1b8d788b57ea82782aa1d03e4924d6bd79c502e7e3df79c37cfdefbb024"
    ),
    "lean-workspace/Strategies/ParityFixtureV1/config.json": (
        "e3430686f13145f992c34462f5d441256579250624bf39ed42b35b2057921137"
    ),
    "contracts/lean-local-parity/v1/2026-07-28.json": (
        "5832d6948bec3e4e672227200bf9e03484e5515afa2c3a14e07682e3200cf6f5"
    ),
    "contracts/lean-local-parity/v1/2026-07-28-open-phase-rerun-1.json": (
        "38bee6caa72b283af1c0f61b5a62c74732803050afad928826768aa632e930c2"
    ),
}


@pytest.fixture(scope="module")
def protocol_bundle() -> ProtocolBundle:
    return load_protocol_bundle()


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "protocol.json"
    path.write_text(deterministic_json(payload), encoding="utf-8", newline="")
    return path


def test_protocol_manifest_is_canonical_versioned_and_content_bound(
    protocol_bundle: ProtocolBundle,
) -> None:
    raw = PROTOCOL_PATH.read_bytes()
    manifest = json.loads(raw)

    assert raw == deterministic_json(manifest).encode("utf-8")
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert protocol_bundle.manifest_sha256 == sha256(raw).hexdigest()
    assert protocol_bundle.manifest == manifest
    assert protocol_bundle.project_source_sha256 == project_source_sha256()
    assert protocol_bundle.public_configuration_sha256 == public_configuration_sha256()
    assert manifest["protocol_name"] == "fixed_parameter_rolling_walk_forward"
    assert manifest["protocol_version"] == "1.0.0"
    assert manifest["schema_version"] == "1.0.0"


def test_protocol_has_the_exact_ordered_unique_nonoverlapping_five_folds(
    protocol_bundle: ProtocolBundle,
) -> None:
    observed = tuple(
        (fold.fold_id, fold.evaluation_start, fold.evaluation_end) for fold in protocol_bundle.folds
    )

    assert FOLDS == EXPECTED_FOLDS
    assert observed == EXPECTED_FOLDS
    assert tuple(fold[0] for fold in EXPECTED_FOLDS) == FOLD_IDS
    assert len(FOLD_IDS) == len(set(FOLD_IDS)) == 5
    intervals = [(date.fromisoformat(start), date.fromisoformat(end)) for _, start, end in observed]
    assert all(start <= end for start, end in intervals)
    assert all(
        prior_end < next_start
        for (_, prior_end), (next_start, _) in zip(intervals, intervals[1:], strict=False)
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "unknown", "duplicate", "overlap", "date-override", "invalid-date", "reordered"],
)
def test_protocol_rejects_any_fold_set_or_date_drift(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    mutation: str,
) -> None:
    manifest = copy.deepcopy(protocol_bundle.manifest)
    folds = manifest["folds"]
    assert isinstance(folds, list)
    if mutation == "missing":
        folds.pop()
    elif mutation == "unknown":
        folds[-1]["fold_id"] = "spy-2026"
    elif mutation == "duplicate":
        folds[-1] = copy.deepcopy(folds[0])
    elif mutation == "overlap":
        folds[1]["evaluation_start"] = "2021-12-31"
    elif mutation == "date-override":
        folds[0]["evaluation_start"] = "2021-01-02"
    elif mutation == "invalid-date":
        folds[0]["evaluation_end"] = "2021-02-30"
    elif mutation == "reordered":
        folds[0], folds[1] = folds[1], folds[0]

    with pytest.raises(WalkForwardContractError):
        load_protocol_bundle(
            _write_manifest(tmp_path, manifest),
            verify_project_files=False,
        )


def test_fold_lookup_rejects_unknown_ids_and_never_accepts_dates(
    protocol_bundle: ProtocolBundle,
) -> None:
    assert fold_by_id(protocol_bundle, "spy-2023").evaluation_end == "2023-12-31"
    for unsupported in ("spy-2020", "SPY-2021", "2021-01-01", "", "../spy-2021"):
        with pytest.raises(WalkForwardContractError, match="unknown walk-forward fold"):
            fold_by_id(protocol_bundle, unsupported)


def test_protocol_binds_the_unchanged_baseline_strategy_risk_cost_and_timing(
    protocol_bundle: ProtocolBundle,
) -> None:
    manifest = protocol_bundle.manifest

    assert manifest["strategy"] == {
        "fast_period": 20,
        "slow_period": 50,
        "target_weight": "0.10",
        "warmup_bars": 50,
    }
    assert manifest["risk"] == {
        "account_type": "cash",
        "automatic_liquidation": False,
        "leverage": "1",
        "long_only": True,
        "max_daily_loss": "0.02",
        "max_drawdown": "0.05",
        "max_gross_exposure": "0.30",
        "max_position_weight": "0.10",
    }
    assert manifest["costs"] == {
        "fee_bps": "1.0",
        "minimum_fee_usd": "1.0",
        "slippage_bps": "2.0",
    }
    assert manifest["capital"] == {"initial_cash_usd": "100000"}
    assert manifest["data"] == {
        "data_normalization": "adjusted",
        "resolution": "daily",
        "symbol": "SPY",
    }
    assert manifest["execution"] == {
        "fill_forward": False,
        "final_signal_expires_without_next_open": True,
        "first_eligible_signal": "completed_trailing_history_only",
        "orders_during_warmup": False,
        "signal_time": "completed_daily_close",
        "timing": "next_market_open",
    }


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("strategy", "fast_period", 19),
        ("strategy", "slow_period", 49),
        ("strategy", "warmup_bars", 49),
        ("strategy", "target_weight", "0.11"),
        ("risk", "account_type", "margin"),
        ("risk", "leverage", "2"),
        ("risk", "long_only", False),
        ("risk", "max_position_weight", "0.11"),
        ("risk", "max_gross_exposure", "0.31"),
        ("risk", "max_daily_loss", "0.03"),
        ("risk", "max_drawdown", "0.06"),
        ("costs", "fee_bps", "0"),
        ("costs", "minimum_fee_usd", "0"),
        ("costs", "slippage_bps", "0"),
        ("data", "symbol", "QQQ"),
        ("data", "resolution", "hour"),
        ("execution", "orders_during_warmup", True),
        ("execution", "timing", "same_close"),
    ],
)
def test_protocol_rejects_strategy_risk_cost_data_or_timing_drift(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    section: str,
    field: str,
    value: object,
) -> None:
    manifest = copy.deepcopy(protocol_bundle.manifest)
    manifest[section][field] = value

    with pytest.raises(WalkForwardContractError, match="fixed v1 contract"):
        load_protocol_bundle(
            _write_manifest(tmp_path, manifest),
            verify_project_files=False,
        )


@pytest.mark.parametrize(
    ("field", "digest", "message"),
    [
        ("source_sha256", "0" * 64, "source_sha256"),
        ("public_configuration_sha256", "1" * 64, "public_configuration_sha256"),
    ],
)
def test_protocol_rejects_project_source_or_configuration_hash_drift(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
    field: str,
    digest: str,
    message: str,
) -> None:
    manifest = copy.deepcopy(protocol_bundle.manifest)
    manifest["project"][field] = digest

    with pytest.raises(WalkForwardContractError, match=message):
        load_protocol_bundle(_write_manifest(tmp_path, manifest))


def test_protocol_rejects_unknown_fields_duplicate_keys_and_noncanonical_bytes(
    tmp_path: Path,
    protocol_bundle: ProtocolBundle,
) -> None:
    unknown = copy.deepcopy(protocol_bundle.manifest)
    unknown["operator_start_date"] = "2021-01-01"
    with pytest.raises(WalkForwardContractError, match="unexpected"):
        load_protocol_bundle(
            _write_manifest(tmp_path, unknown),
            verify_project_files=False,
        )

    duplicate = PROTOCOL_PATH.read_text(encoding="utf-8").replace(
        '  "protocol_name": "fixed_parameter_rolling_walk_forward",',
        '  "protocol_name": "fixed_parameter_rolling_walk_forward",\n'
        '  "protocol_name": "fixed_parameter_rolling_walk_forward",',
        1,
    )
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(duplicate, encoding="utf-8", newline="")
    with pytest.raises(WalkForwardContractError, match="duplicate"):
        load_protocol_bundle(duplicate_path, verify_project_files=False)

    spaced_path = tmp_path / "spaced.json"
    spaced_path.write_text(json.dumps(protocol_bundle.manifest), encoding="utf-8", newline="")
    with pytest.raises(WalkForwardContractError, match="sorted deterministic JSON"):
        load_protocol_bundle(spaced_path, verify_project_files=False)


def test_schemas_use_sorted_deterministic_json_bytes() -> None:
    for path in (
        walk_forward_contract.PROTOCOL_SCHEMA_PATH,
        walk_forward_contract.OBSERVATION_SCHEMA_PATH,
        walk_forward_contract.AGGREGATE_SCHEMA_PATH,
    ):
        raw = path.read_bytes()
        payload = json.loads(raw)
        assert raw == deterministic_json(payload).encode("utf-8"), path.name


def test_schemas_are_closed_versioned_and_bind_exact_v1_constants() -> None:
    protocol_schema = json.loads(walk_forward_contract.PROTOCOL_SCHEMA_PATH.read_text())
    observation_schema = json.loads(walk_forward_contract.OBSERVATION_SCHEMA_PATH.read_text())
    aggregate_schema = json.loads(walk_forward_contract.AGGREGATE_SCHEMA_PATH.read_text())

    assert protocol_schema["additionalProperties"] is False
    assert observation_schema["additionalProperties"] is False
    assert aggregate_schema["additionalProperties"] is False
    assert protocol_schema["properties"]["folds"]["const"] == [
        {
            "evaluation_end": end,
            "evaluation_start": start,
            "fold_id": fold_id,
        }
        for fold_id, start, end in EXPECTED_FOLDS
    ]
    assert observation_schema["properties"]["strategy"]["const"] == (
        walk_forward_contract.EXPECTED_STRATEGY
    )
    assert observation_schema["properties"]["risk"]["const"] == (
        walk_forward_contract.EXPECTED_RISK
    )
    assert aggregate_schema["properties"]["fold_results"]["minItems"] == 5
    assert aggregate_schema["properties"]["fold_results"]["maxItems"] == 5


def test_result_allowlist_and_identity_denylist_are_exact_and_disjoint(
    protocol_bundle: ProtocolBundle,
) -> None:
    assert tuple(protocol_bundle.manifest["permitted_result_fields"]) == PERMITTED_RESULT_FIELDS
    assert tuple(protocol_bundle.manifest["prohibited_identity_fields"]) == (
        PROHIBITED_IDENTITY_FIELDS
    )
    assert set(PERMITTED_RESULT_FIELDS).isdisjoint(PROHIBITED_IDENTITY_FIELDS)
    for required in (
        "account_id",
        "backtest_id",
        "billing_data",
        "cloud_id",
        "credentials",
        "email",
        "license",
        "machine_path",
        "organization_id",
        "project_id",
        "token",
        "url",
    ):
        assert required in PROHIBITED_IDENTITY_FIELDS


def test_existing_baseline_and_parity_files_and_dimensions_remain_immutable() -> None:
    observed = {
        relative: sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in IMMUTABLE_FILE_SHA256
    }

    assert observed == IMMUTABLE_FILE_SHA256
    assert len(COMPARISON_DIMENSIONS) == 16
    assert COMPARISON_DIMENSIONS == EXPECTED_PARITY_DIMENSIONS


def test_schemas_encode_exact_identity_lists_metric_bounds_fold_coupling_and_order() -> None:
    protocol_schema = json.loads(walk_forward_contract.PROTOCOL_SCHEMA_PATH.read_text())
    observation_schema = json.loads(walk_forward_contract.OBSERVATION_SCHEMA_PATH.read_text())
    aggregate_schema = json.loads(walk_forward_contract.AGGREGATE_SCHEMA_PATH.read_text())

    assert protocol_schema["properties"]["permitted_result_fields"]["const"] == list(
        PERMITTED_RESULT_FIELDS
    )
    assert protocol_schema["properties"]["prohibited_identity_fields"]["const"] == list(
        PROHIBITED_IDENTITY_FIELDS
    )

    drawdown_rule = observation_schema["properties"]["metrics"]["properties"]["maximum_drawdown"]
    drawdown_ref = drawdown_rule["$ref"]
    assert drawdown_ref.startswith("#/$defs/")
    drawdown_pattern = observation_schema["$defs"][drawdown_ref.rsplit("/", 1)[-1]]["pattern"]
    assert re.fullmatch(drawdown_pattern, "0")
    assert re.fullmatch(drawdown_pattern, "0.5")
    assert re.fullmatch(drawdown_pattern, "1")
    assert re.fullmatch(drawdown_pattern, "1.0001") is None
    assert re.fullmatch(drawdown_pattern, "2") is None

    expected_bindings = {fold_id: (start, end) for fold_id, start, end in EXPECTED_FOLDS}
    observed_bindings: dict[str, tuple[str, str]] = {}
    for clause in observation_schema.get("allOf", []):
        condition = clause.get("if", {}).get("properties", {})
        consequence = clause.get("then", {}).get("properties", {})
        fold_id = condition.get("fold_id", {}).get("const")
        start = consequence.get("evaluation_start", {}).get("const")
        end = consequence.get("evaluation_end", {}).get("const")
        if isinstance(fold_id, str) and isinstance(start, str) and isinstance(end, str):
            observed_bindings[fold_id] = (start, end)
    assert observed_bindings == expected_bindings

    fold_results = aggregate_schema["properties"]["fold_results"]
    prefix_items = fold_results["prefixItems"]
    assert fold_results["items"] is False
    assert fold_results["minItems"] == fold_results["maxItems"] == 5
    assert len(prefix_items) == 5

    def nested_const(value: object, field: str) -> str | None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                selected = properties.get(field)
                if isinstance(selected, dict) and isinstance(selected.get("const"), str):
                    return str(selected["const"])
            for child in value.values():
                found = nested_const(child, field)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = nested_const(child, field)
                if found is not None:
                    return found
        return None

    assert [nested_const(item, "fold_id") for item in prefix_items] == list(FOLD_IDS)
    assert [nested_const(item, "evaluation_start") for item in prefix_items] == [
        start for _, start, _ in EXPECTED_FOLDS
    ]
    assert [nested_const(item, "evaluation_end") for item in prefix_items] == [
        end for _, _, end in EXPECTED_FOLDS
    ]
