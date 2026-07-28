from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "lean-workspace"
SKELETON = WORKSPACE / "Strategies" / "SkeletonBacktest"
BASELINE = WORKSPACE / "Strategies" / "MovingAverageBaseline"
PROJECTS = (SKELETON, BASELINE)

ALLOWED_METADATA_KEYS = {
    "cloud-id",
    "local-id",
    "organization-id",
}
FORBIDDEN_CONFIG_KEYS = {
    "access-token",
    "api-token",
    "api-key",
    "api-secret",
    "brokerage",
    "brokerage-name",
    "credentials",
    "data-channel-provider",
    "data-download",
    "data-folder",
    "data-provider",
    "deploy",
    "download-data",
    "encryption-key-path",
    "encryption-key",
    "job-organization-id",
    "live-id",
    "live-mode",
    "live-node-id",
    "optimization-id",
    "optimizer",
    "passphrase",
    "password",
    "private-key",
    "private-key-path",
    "refresh-token",
    "secret-key",
    "user-id",
}
FORBIDDEN_CALLS = {
    "buy",
    "liquidate",
    "market_order",
    "sell",
    "set_holdings",
}


def _source(project: Path) -> str:
    return (project / "main.py").read_text(encoding="utf-8")


def _call_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _normalized_json_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).strip().lower().replace("_", "-"))
            keys.update(_normalized_json_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_normalized_json_keys(child))
    return keys


def _assert_public_safe_project_config(config: dict[str, object]) -> None:
    required_keys = {"algorithm-language", "parameters", "description"}
    assert required_keys.issubset(config)
    assert set(config).issubset(required_keys | ALLOWED_METADATA_KEYS)
    assert FORBIDDEN_CONFIG_KEYS.isdisjoint(_normalized_json_keys(config))
    for key in ALLOWED_METADATA_KEYS & config.keys():
        value = config[key]
        assert value is None or (not isinstance(value, bool) and isinstance(value, (int, str)))


def _load_with_algorithm_stubs(monkeypatch: pytest.MonkeyPatch, path: Path) -> ModuleType:
    algorithm_imports = ModuleType("AlgorithmImports")
    fee_model = type("FeeModel", (), {})
    qc_algorithm = type("QCAlgorithm", (), {})
    stub_names = {
        "AccountType": type("AccountType", (), {}),
        "BrokerageName": type("BrokerageName", (), {}),
        "CashAmount": type("CashAmount", (), {}),
        "ConstantSlippageModel": type("ConstantSlippageModel", (), {}),
        "DataNormalizationMode": type("DataNormalizationMode", (), {}),
        "FeeModel": fee_model,
        "OrderFee": type("OrderFee", (), {}),
        "OrderStatus": type("OrderStatus", (), {}),
        "QCAlgorithm": qc_algorithm,
        "Resolution": type("Resolution", (), {}),
        "Slice": type("Slice", (), {}),
        "TimeZones": type("TimeZones", (), {}),
    }
    for name, value in stub_names.items():
        setattr(algorithm_imports, name, value)
    monkeypatch.setitem(sys.modules, "AlgorithmImports", algorithm_imports)

    module_name = f"lean_test_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("project", PROJECTS)
def test_project_sources_parse_and_configs_are_public_safe(project: Path) -> None:
    source = _source(project)
    ast.parse(source)
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))

    assert config["algorithm-language"] == "Python"
    _assert_public_safe_project_config(config)
    assert config["parameters"]
    assert all(isinstance(value, str) for value in config["parameters"].values())
    assert "live mode forbidden" in config["description"].lower()
    assert "def initialize(" in source
    assert "def on_data(" in source
    assert "self.live_mode" in source
    assert "raise RuntimeError" in source
    assert '"SPY"' in source or config["parameters"].get("symbol") == "SPY"
    assert "Resolution.DAILY" in source
    assert "fill_forward=False" in source
    assert "AccountType.CASH" in source
    assert "BrokerageName.QUANT_CONNECT_BROKERAGE" in source
    assert "BrokerageName.QUANTCONNECT_BROKERAGE" not in source
    assert "security.set_leverage(1.0)" in source
    assert "self.set_benchmark" in source
    assert "def Initialize(" not in source
    assert "def OnData(" not in source


def test_project_metadata_ids_are_optional_but_runtime_configuration_is_forbidden() -> None:
    base: dict[str, object] = {
        "algorithm-language": "Python",
        "parameters": {"start-date": "2023-01-01"},
        "description": "Backtest-only example.",
    }
    for key, value in (
        ("organization-id", "metadata-only"),
        ("cloud-id", 123),
        ("local-id", None),
    ):
        _assert_public_safe_project_config({**base, key: value})

    for forbidden_key in ("api-token", "live-mode", "brokerage", "data-provider"):
        with pytest.raises(AssertionError):
            _assert_public_safe_project_config({**base, forbidden_key: "forbidden"})


def test_skeleton_is_no_order_and_has_five_percent_cap() -> None:
    source = _source(SKELETON)
    config = json.loads((SKELETON / "config.json").read_text(encoding="utf-8"))

    assert FORBIDDEN_CALLS.isdisjoint(_call_names(source))
    assert "market_on_open_order" not in _call_names(source)
    assert float(config["parameters"]["maximum-allocation"]) <= 0.05
    assert "orders=0" in source


def test_baseline_has_separated_components_and_only_next_open_orders() -> None:
    source = _source(BASELINE)
    calls = _call_names(source)

    for class_name in (
        "MovingAverageSignalModel",
        "LongOnlyPortfolioModel",
        "LatchedRiskModel",
        "NextOpenExecutionModel",
    ):
        assert f"class {class_name}" in source
    assert FORBIDDEN_CALLS.isdisjoint(calls)
    assert "market_on_open_order" in calls
    assert "ConstantSlippageModel" in source
    assert "BpsMinimumFeeModel" in source
    assert "self.set_warm_up" in source
    assert "self.is_warming_up" in source
    assert "bar.is_fill_forward" in source
    assert "cancel_open_orders" in calls
    assert "on_end_of_algorithm" in source
    assert "get_next_market_open" in calls
    assert "next_open.date() <= self._configured_end_date" in source


def test_baseline_config_uses_positive_bounded_string_parameters() -> None:
    config = json.loads((BASELINE / "config.json").read_text(encoding="utf-8"))
    parameters = config["parameters"]

    assert int(parameters["fast-period"]) < int(parameters["slow-period"])
    assert int(parameters["warmup-bars"]) >= int(parameters["slow-period"])
    assert 0 < float(parameters["target-weight"]) <= 0.10
    assert 0 < float(parameters["max-position-weight"]) <= 0.10
    assert 0 < float(parameters["max-total-exposure"]) <= 0.30
    assert 0 < float(parameters["max-daily-loss"]) <= 0.02
    assert 0 < float(parameters["max-drawdown"]) <= 0.05
    assert float(parameters["fee-bps"]) > 0
    assert float(parameters["minimum-fee"]) > 0
    assert float(parameters["slippage-bps"]) > 0


def test_pure_signal_fee_and_parameter_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_with_algorithm_stubs(monkeypatch, BASELINE / "main.py")

    signal = module.MovingAverageSignalModel(2, 3, 0.10)
    assert signal.target_for_completed_closes((1.0, 2.0)) is None
    assert signal.target_for_completed_closes((1.0, 2.0, 3.0)) == pytest.approx(0.10)
    assert signal.target_for_completed_closes((3.0, 2.0, 1.0)) == 0.0
    assert module.compute_bps_minimum_fee(1_000.0, 1.0, 1.0) == pytest.approx(1.0)
    assert module.compute_bps_minimum_fee(1_000_000.0, 1.0, 1.0) == pytest.approx(100.0)
    assert module.parse_positive_int("20", "fast") == 20
    assert module.parse_nonnegative_int("0", "warmup-bars") == 0
    assert module.parse_nonnegative_float("0", "minimum-fee") == 0
    assert module.parse_equity_ticker("parity") == "PARITY"
    with pytest.raises(ValueError):
        module.parse_positive_int("20.0", "fast")
    with pytest.raises(ValueError):
        module.parse_positive_float("nan", "fee")
    with pytest.raises(ValueError):
        module.parse_equity_ticker("../SPY")
    with pytest.raises(ValueError):
        module.LongOnlyPortfolioModel(0.11, 0.30)


def test_pure_risk_model_uses_prior_close_and_latches(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_with_algorithm_stubs(monkeypatch, BASELINE / "main.py")
    risk = module.LatchedRiskModel(100.0, 0.02, 0.05)

    assert risk.close_session("2024-01-01", 100.0) == ()
    assert risk.observe("2024-01-02", 97.9) == ("daily_loss",)
    assert risk.halted
    assert risk.observe("2024-01-03", 120.0) == ("daily_loss",)

    drawdown = module.LatchedRiskModel(100.0, 0.02, 0.05)
    assert drawdown.close_session("2024-01-01", 110.0) == ()
    reasons = drawdown.observe("2024-01-02", 104.0)
    assert "max_drawdown" in reasons
