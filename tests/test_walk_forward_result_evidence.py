from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from trading_bot_lab.parity.contract import deterministic_json
from trading_bot_lab.walk_forward.contract import FOLD_IDS
from trading_bot_lab.walk_forward.result_json import load_result_aggregate

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "contracts" / "walk-forward" / "v1" / "2026-07-29-result-aggregate.json"
EVIDENCE_SHA256 = "f8ad1fa47b03862835d032edadcb1ce684ec9d695dcc72b03bd27fdd15ba933e"
EXPECTED_SUMMARY = {
    "benchmark_beating_fold_count": 1,
    "completed_fold_count": 5,
    "median_benchmark_return": "0.248862356640464819099751101",
    "median_excess_return": "-0.232121822640464819099751101",
    "median_probabilistic_sharpe_ratio": "0",
    "median_sharpe_ratio": "-4.5968",
    "median_sortino_ratio": "-4.6691",
    "median_strategy_return": "0.01084085",
    "positive_return_fold_count": 4,
    "total_fees_usd": "85",
    "total_orders": 85,
    "worst_fold_return": "-0.025180004",
    "worst_maximum_drawdown": "0.027",
}
FORBIDDEN_TEXT = (
    "account_email",
    "account_metadata",
    "backtest_id",
    "billing",
    "broker_id",
    "cloud_id",
    "credential",
    "hostname",
    "order_id",
    "organization_id",
    "owner_name",
    "project_id",
    "raw_order_id",
    "subscription",
    "token",
    "http://",
    "https://",
    "/home/",
    "c:\\\\",
)


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_float(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_float(nested)


def test_tracked_result_aggregate_is_deterministic_private_safe_and_recomputed() -> None:
    raw = EVIDENCE_PATH.read_bytes()
    text = raw.decode("utf-8")
    payload = json.loads(text)

    assert sha256(raw).hexdigest() == EVIDENCE_SHA256
    assert text == deterministic_json(payload)
    assert load_result_aggregate(EVIDENCE_PATH) == payload
    assert payload["record_type"] == "walk_forward_result_aggregate"
    assert payload["contract_status"] == "walk_forward_result_contract_complete"
    assert payload["source_formats"] == ["quantconnect_result_json"]
    assert payload["summary"] == EXPECTED_SUMMARY
    _assert_no_float(payload)

    folds = payload["fold_results"]
    assert [fold["fold_id"] for fold in folds] == list(FOLD_IDS)
    assert [fold["configuration"]["name"] for fold in folds] == [
        f"wf-v1-{fold_id}" for fold_id in FOLD_IDS
    ]
    for fold in folds:
        assert fold["state"] == {"completion_status": "completed"}
        assert fold["configuration"]["account_type"] == "cash"
        assert fold["configuration"]["account_currency"] == "USD"
        assert fold["configuration"]["out_of_sample_days"] == 0
        assert fold["configuration"]["parameters"] == {
            "fold-id": fold["fold_id"],
            "optimization-mode": "false",
        }
        reported = fold["metrics"]["directly_reported"]
        assert reported["fee_validation_source"] == "overview_runtime_rounded"
        assert reported["fee_precision"] == "rounded_to_cent"
        assert reported["order_event_fee_evidence_available"] is False
        assert fold["orders"]["order_validation_source"] == "completed_orders"
        assert fold["orders"]["final_position_state"] == "long"
        assert "order_event_detail" in fold["metrics"]["unavailable"]

    lowered = text.casefold()
    assert all(forbidden not in lowered for forbidden in FORBIDDEN_TEXT)
    assert "orderEvents" not in text
    assert "tradeStatistics" not in text
