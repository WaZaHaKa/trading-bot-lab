from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from trading_bot_lab.parity import (
    ParityMismatchError,
    build_local_parity_trace,
    compare_parity_traces,
)
from trading_bot_lab.parity.compare import COMPARISON_DIMENSIONS
from trading_bot_lab.parity.contract import load_contract_bundle

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "lean-workspace"
SKELETON = WORKSPACE / "Strategies" / "SkeletonBacktest"
BASELINE = WORKSPACE / "Strategies" / "MovingAverageBaseline"
PARITY = WORKSPACE / "Strategies" / "ParityFixtureV1"
PROJECTS = (SKELETON, BASELINE)

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
    assert set(config) == required_keys
    assert FORBIDDEN_CONFIG_KEYS.isdisjoint(_normalized_json_keys(config))


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


def _load_parity_with_algorithm_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    algorithm_imports = ModuleType("AlgorithmImports")

    class SubscriptionTransportMedium:
        LOCAL_FILE = "local-file-enum"
        OBJECT_STORE = "object-store-enum"

    class Resolution:
        DAILY = "daily"

    class SubscriptionDataSource:
        def __init__(self, source: str, medium: object, file_format: object) -> None:
            self.source = source
            self.transport_medium = medium
            self.format = file_format

    class FileFormat:
        CSV = "csv"

    class EmptyBase:
        pass

    class ImmediateFillModel:
        def __init__(self) -> None:
            pass

    stub_names = {
        "AccountType": type("AccountType", (), {"CASH": "cash"}),
        "BrokerageName": type(
            "BrokerageName",
            (),
            {"QUANT_CONNECT_BROKERAGE": "quant-connect"},
        ),
        "CashAmount": EmptyBase,
        "FeeModel": EmptyBase,
        "FileFormat": FileFormat,
        "Globals": type("Globals", (), {"data_folder": "/lean/data", "version": "2.5.0"}),
        "ImmediateFillModel": ImmediateFillModel,
        "OrderFee": EmptyBase,
        "OrderStatus": type(
            "OrderStatus",
            (),
            {"FILLED": "filled", "PARTIALLY_FILLED": "partially-filled"},
        ),
        "PythonData": EmptyBase,
        "QCAlgorithm": EmptyBase,
        "Resolution": Resolution,
        "Slice": EmptyBase,
        "SubscriptionDataSource": SubscriptionDataSource,
        "SubscriptionTransportMedium": SubscriptionTransportMedium,
        "TimeZones": type("TimeZones", (), {"UTC": "utc"}),
    }
    for name, value in stub_names.items():
        setattr(algorithm_imports, name, value)
    monkeypatch.setitem(sys.modules, "AlgorithmImports", algorithm_imports)

    path = PARITY / "main.py"
    module_name = "lean_test_ParityFixtureV1"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _open_snapshot(
    module: ModuleType,
    *,
    timestamp: str = "2024-01-09T00:00:00+00:00",
    open_price: Decimal = Decimal("98"),
    cash: Decimal = Decimal("90013.0048"),
    quantity: Decimal = Decimal("96"),
    previous_close_equity: Decimal = Decimal("99517.0048"),
    previous_peak_equity: Decimal = Decimal("100000"),
) -> object:
    return module.value_open_phase_risk_snapshot(
        execution_timestamp=timestamp,
        open_mark=module.ExecutionOpenMark(timestamp=timestamp, price=open_price),
        current_cash=cash,
        current_quantity=quantity,
        previous_close_equity=previous_close_equity,
        previous_peak_equity=previous_peak_equity,
    )


def _risk_algorithm(
    module: ModuleType,
    *,
    timestamp: str,
    prior_decision_count: int = 0,
) -> object:
    algorithm = module.ParityFixtureV1()
    algorithm._risk_decisions = [{} for _ in range(prior_decision_count)]
    algorithm._fixture_bars = (SimpleNamespace(timestamp=timestamp),)
    algorithm._bar_index = 0
    return algorithm


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


def test_project_metadata_ids_and_runtime_configuration_are_forbidden() -> None:
    base: dict[str, object] = {
        "algorithm-language": "Python",
        "parameters": {"start-date": "2023-01-01"},
        "description": "Backtest-only example.",
    }
    for forbidden_key in (
        "organization-id",
        "cloud-id",
        "local-id",
        "api-token",
        "live-mode",
        "brokerage",
        "data-provider",
    ):
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


def test_parity_project_is_public_safe_backtest_only_and_scenario_fixed() -> None:
    source = _source(PARITY)
    ast.parse(source)
    config = json.loads((PARITY / "config.json").read_text(encoding="utf-8"))

    _assert_public_safe_project_config(config)
    assert config["algorithm-language"] == "Python"
    assert config["parameters"] == {
        "data-transport": "local-file",
        "object-store-key": "",
    }
    assert "live mode forbidden" in config["description"].lower()
    assert "self.live_mode" in source
    assert "self.time.date() != expected.session" in source
    assert "data-age" not in config["parameters"]
    assert "security.set_leverage(1.0)" in source
    assert source.count("self.set_benchmark(self._symbol)") == 1
    assert "AccountType.CASH" in source
    assert "market_order" in _call_names(source)
    assert {"buy", "sell", "liquidate", "set_holdings"}.isdisjoint(_call_names(source))
    assert "REMOTE_FILE" not in source
    assert "SubscriptionTransportMedium.REST" not in source
    assert "SubscriptionTransportMedium.STREAMING" not in source
    assert "object_store.save" not in source
    assert "object_store.delete" not in source
    assert "requests" not in source
    assert "urllib" not in source
    assert "http://" not in source and "https://" not in source


def test_parity_project_constants_match_versioned_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    scenario_path = ROOT / "tests" / "fixtures" / "parity" / "v1" / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    fixture_path = scenario_path.parent / scenario["fixture"]
    contract_root = ROOT / "contracts" / "parity" / "v1"

    assert scenario["scenario_manifest_version"] == module.SCENARIO_MANIFEST_VERSION
    assert scenario["scenario_id"] == module.SCENARIO_ID
    assert scenario["symbol"] == module.SYMBOL
    assert scenario["timeframe_seconds"] == module.TIMEFRAME_SECONDS
    assert scenario["fixture"] == module.FIXTURE_NAME
    assert sha256(fixture_path.read_bytes()).hexdigest() == module.FIXTURE_SHA256
    assert (
        sha256((contract_root / "contract.json").read_bytes()).hexdigest() == module.CONTRACT_SHA256
    )
    assert sha256(scenario_path.read_bytes()).hexdigest() == module.SCENARIO_MANIFEST_SHA256
    assert (
        sha256((contract_root / "scenario.schema.json").read_bytes()).hexdigest()
        == module.SCENARIO_SCHEMA_SHA256
    )
    assert (
        sha256((contract_root / "trace.schema.json").read_bytes()).hexdigest()
        == module.TRACE_SCHEMA_SHA256
    )
    assert module._assumptions() == {
        "backtest": scenario["assumptions"],
        "risk": scenario["risk"],
    }
    assert scenario["strategy"]["fast_window"] == module.FAST_WINDOW
    assert scenario["strategy"]["slow_window"] == module.SLOW_WINDOW
    assert module.canonical_decimal(module.TARGET_WEIGHT) == scenario["strategy"]["target_weight"]


def test_parity_fixture_parser_binds_exact_lf_bytes_and_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    payload = fixture.read_bytes()
    bars = module.parse_fixture_bytes(payload)

    assert b"\r" not in payload and payload.endswith(b"\n")
    assert len(bars) == 8
    assert bars[0].timestamp == "2024-01-02T00:00:00+00:00"
    assert bars[-1].timestamp == "2024-01-11T00:00:00+00:00"
    assert all(bar.symbol == "PARITY" for bar in bars)
    with pytest.raises(ValueError, match="SHA-256"):
        module.parse_fixture_bytes(payload + b"\n")
    crlf = payload.replace(b"\n", b"\r\n")
    with pytest.raises(ValueError, match="LF line endings"):
        module.parse_fixture_bytes(crlf, expected_sha256=sha256(crlf).hexdigest())


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            b"2024-01-03,PARITY,100.00,102.00,99.00,101.00,1010",
            b"2024-01-02,PARITY,100.00,102.00,99.00,101.00,1010",
            "duplicated timestamp",
        ),
        (
            b"2024-01-04,PARITY,101.00,104.00,100.00,103.00,1020",
            b"2024-01-01,PARITY,101.00,104.00,100.00,103.00,1020",
            "sorted ascending",
        ),
        (
            b"2024-01-05,PARITY,104.00,105.00,103.00,104.00,1030",
            b"2024-01-05,PARITY,104.00,103.00,103.00,104.00,1030",
            "high is below",
        ),
        (
            b"2024-01-08,PARITY,103.00,104.00,98.00,99.00,1040",
            b"2024-01-08,PARITY,NaN,104.00,98.00,99.00,1040",
            "positive and finite",
        ),
    ],
)
def test_parity_fixture_parser_rejects_structural_mutations(
    monkeypatch: pytest.MonkeyPatch,
    old: bytes,
    new: bytes,
    message: str,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    mutated = fixture.read_bytes().replace(old, new)

    with pytest.raises(ValueError, match=message):
        module.parse_fixture_bytes(mutated, expected_sha256=sha256(mutated).hexdigest())


def test_parity_transport_is_explicit_fixed_and_cross_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    local = module.resolve_transport("local-file", "", "/lean/data")
    windows = module.resolve_transport("local-file", "", r"C:\Lean\Data")
    stored = module.resolve_transport(
        "object-store",
        module.OBJECT_STORE_KEY,
        "/unused",
    )

    assert local.source == "/lean/data/custom/parity/v1/synthetic_weekdays.csv"
    assert local.medium == "local-file-enum"
    assert windows.source == r"C:\Lean\Data\custom\parity\v1\synthetic_weekdays.csv"
    assert stored.source == "trading-bot-lab/parity/v1/synthetic_weekdays.csv"
    assert stored.medium == "object-store-enum"
    with pytest.raises(ValueError, match="remain empty"):
        module.resolve_transport("local-file", module.OBJECT_STORE_KEY, "/lean/data")
    for unsafe in (
        "",
        "wrong/key.csv",
        "../synthetic_weekdays.csv",
        "https://example.invalid/fixture.csv",
    ):
        with pytest.raises(ValueError, match="fixed public"):
            module.resolve_transport("object-store", unsafe, "/lean/data")
    for unsupported in ("remote-file", "rest", "streaming", "LOCAL-FILE", ""):
        with pytest.raises(ValueError, match="exactly"):
            module.resolve_transport(unsupported, "", "/lean/data")


def test_parity_object_store_read_has_no_discovery_write_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    payload = fixture.read_bytes()

    class FakeObjectStore:
        def __init__(self, present: bool) -> None:
            self.present = present
            self.calls: list[tuple[str, str]] = []

        def contains_key(self, key: str) -> bool:
            self.calls.append(("contains", key))
            return self.present

        def read_bytes(self, key: str) -> list[int]:
            self.calls.append(("read", key))
            return list(payload)

    selection = module.resolve_transport(
        "object-store",
        module.OBJECT_STORE_KEY,
        "/unused",
    )
    missing = FakeObjectStore(False)
    with pytest.raises(ValueError, match="key is missing"):
        module.read_fixture_source(selection, missing)
    assert missing.calls == [("contains", module.OBJECT_STORE_KEY)]

    present = FakeObjectStore(True)
    assert module.read_fixture_source(selection, present) == payload
    assert present.calls == [
        ("contains", module.OBJECT_STORE_KEY),
        ("read", module.OBJECT_STORE_KEY),
    ]


def test_parity_custom_data_uses_same_parser_for_both_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    bars = module.parse_fixture_bytes(fixture.read_bytes())
    config = type("Config", (), {"symbol": "PARITY"})()

    observed: list[tuple[float, float, str]] = []
    for selection in (
        module.resolve_transport("local-file", "", "/lean/data"),
        module.resolve_transport("object-store", module.OBJECT_STORE_KEY, "/unused"),
    ):
        module.ParityFixtureData.configure(selection, bars)
        parser = module.ParityFixtureData()
        assert parser.reader(config, module.FIXTURE_HEADER, None, False) is None
        point = parser.reader(config, bars[0].source_line, None, False)
        observed.append((point.open, point.close, point.session_timestamp))
        source = parser.get_source(config, None, False)
        assert source.source == selection.source
        assert source.transport_medium == selection.medium
    assert observed[0] == observed[1]


def test_parity_signal_is_trailing_next_row_only_and_final_signal_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    bars = module.parse_fixture_bytes(fixture.read_bytes())
    state = module.TrailingSignalState()
    executions: list[tuple[str, str, str]] = []

    for bar in bars:
        pending = state.begin_bar(bar.timestamp)
        if pending is not None:
            executions.append(
                (pending.timestamp, bar.timestamp, module.canonical_decimal(pending.target_weight))
            )
            assert pending.timestamp < bar.timestamp
        state.finish_bar(bar.timestamp, bar.close)

    assert executions[0] == (
        "2024-01-04T00:00:00+00:00",
        "2024-01-05T00:00:00+00:00",
        "0.1",
    )
    assert state.pending is not None
    assert state.pending.timestamp == "2024-01-11T00:00:00+00:00"
    assert module.canonical_decimal(state.pending.target_weight) == "0.1"


def test_parity_cost_aware_sizing_matches_local_integer_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)

    assert module.cost_aware_target_quantity(
        target_weight=Decimal("0.1"),
        reference_price=Decimal("104"),
        current_quantity=Decimal("0"),
        cash=Decimal("100000"),
    ) == Decimal("96")
    assert module.cost_aware_target_quantity(
        target_weight=Decimal("0.1"),
        reference_price=Decimal("103"),
        current_quantity=Decimal("96"),
        cash=Decimal("90013.0048"),
    ) == Decimal("96")
    assert module.cost_aware_target_quantity(
        target_weight=Decimal("0"),
        reference_price=Decimal("98"),
        current_quantity=Decimal("96"),
        cash=Decimal("90013.0048"),
    ) == Decimal("0")
    assert "calculate_order_quantity" not in _source(PARITY)


def test_parity_open_phase_snapshot_uses_explicit_open_and_updates_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    snapshot = _open_snapshot(module)

    assert snapshot.execution_timestamp == "2024-01-09T00:00:00+00:00"
    assert snapshot.current_cash == Decimal("90013.0048")
    assert snapshot.current_quantity == Decimal("96")
    assert snapshot.current_exposure == Decimal("9408.00000000")
    assert snapshot.current_equity == Decimal("99421.00480000")
    assert snapshot.start_of_day_equity == Decimal("99517.0048")
    assert snapshot.peak_equity == Decimal("100000")
    assert (
        snapshot.daily_loss_pct
        == (snapshot.start_of_day_equity - snapshot.current_equity) / snapshot.start_of_day_equity
    )
    assert (
        snapshot.drawdown_pct
        == (snapshot.peak_equity - snapshot.current_equity) / snapshot.peak_equity
    )
    assert snapshot.order_weight(Decimal("9406.1184")) == (
        Decimal("9406.1184") / snapshot.current_equity
    )

    changed_open = _open_snapshot(module, open_price=Decimal("97"))
    assert changed_open.current_equity != snapshot.current_equity
    assert changed_open.daily_loss_pct != snapshot.daily_loss_pct
    assert changed_open.drawdown_pct != snapshot.drawdown_pct
    assert changed_open.order_weight(Decimal("9406.1184")) != snapshot.order_weight(
        Decimal("9406.1184")
    )

    new_peak = _open_snapshot(module, open_price=Decimal("1100"))
    assert new_peak.peak_equity == new_peak.current_equity
    assert new_peak.drawdown_pct == 0

    half_even = _open_snapshot(
        module,
        open_price=Decimal("1.000000005"),
        cash=Decimal("1"),
        quantity=Decimal("1"),
        previous_close_equity=Decimal("10"),
        previous_peak_equity=Decimal("10"),
    )
    carry = _open_snapshot(
        module,
        open_price=Decimal("1.000000015"),
        cash=Decimal("1"),
        quantity=Decimal("1"),
        previous_close_equity=Decimal("10"),
        previous_peak_equity=Decimal("10"),
    )
    assert half_even.current_exposure == Decimal("1.00000000")
    assert carry.current_exposure == Decimal("1.00000002")


def test_parity_open_phase_snapshot_ignores_close_and_rejects_future_marks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    bar = module.parse_fixture_bytes(fixture.read_bytes())[5]
    changed_close = bar._replace(close=Decimal("1000000"))

    original = _open_snapshot(module, timestamp=bar.timestamp, open_price=bar.open)
    close_changed = _open_snapshot(
        module,
        timestamp=changed_close.timestamp,
        open_price=changed_close.open,
    )
    assert original == close_changed

    with pytest.raises(ValueError, match="execution timestamp"):
        module.value_open_phase_risk_snapshot(
            execution_timestamp=bar.timestamp,
            open_mark=module.ExecutionOpenMark(
                timestamp="2024-01-10T00:00:00+00:00",
                price=bar.open,
            ),
            current_cash=Decimal("90013.0048"),
            current_quantity=Decimal("96"),
            previous_close_equity=Decimal("99517.0048"),
            previous_peak_equity=Decimal("100000"),
        )
    with pytest.raises(ValueError, match="explicit execution-open mark"):
        module.value_open_phase_risk_snapshot(
            execution_timestamp=bar.timestamp,
            open_mark=bar.open,
            current_cash=Decimal("90013.0048"),
            current_quantity=Decimal("96"),
            previous_close_equity=Decimal("99517.0048"),
            previous_peak_equity=Decimal("100000"),
        )


def test_parity_on_data_builds_open_snapshot_before_pending_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    fixture = ROOT / "tests" / "fixtures" / "parity" / "v1" / "synthetic_weekdays.csv"
    bar = module.parse_fixture_bytes(fixture.read_bytes())[5]
    point = SimpleNamespace(session_timestamp=bar.timestamp, value=None)

    class Data:
        def contains_key(self, symbol: str) -> bool:
            return symbol == "PARITY"

        def __getitem__(self, symbol: str) -> object:
            assert symbol == "PARITY"
            return point

    class Security:
        def __init__(self) -> None:
            self.marks: list[float] = []

        def set_market_price(self, selected: object) -> None:
            self.marks.append(selected.value)

    class Portfolio:
        cash = Decimal("90013.0048")

        def __getitem__(self, symbol: str) -> object:
            assert symbol == "PARITY"
            return SimpleNamespace(quantity=Decimal("96"))

    class SignalState:
        def begin_bar(self, timestamp: str) -> object:
            assert timestamp == bar.timestamp
            return SimpleNamespace(
                timestamp="2024-01-08T00:00:00+00:00",
                target_weight=Decimal("0"),
            )

        def finish_bar(self, timestamp: str, close: Decimal) -> Decimal:
            assert timestamp == bar.timestamp
            assert close == bar.close
            return Decimal("0")

    security = Security()
    algorithm = module.ParityFixtureV1()
    algorithm._symbol = "PARITY"
    algorithm._fixture_bars = (bar,)
    algorithm._bar_index = 0
    algorithm.time = datetime(2024, 1, 9)
    algorithm._security = security
    algorithm.portfolio = Portfolio()
    algorithm._last_close_equity = Decimal("99517.0048")
    algorithm._peak_equity = Decimal("100000")
    algorithm._halt_reasons = ()
    algorithm._signal_state = SignalState()
    executions: list[tuple[object, object, object, Decimal]] = []
    closes: list[tuple[object, Decimal]] = []
    algorithm._execute_pending = lambda pending, selected, snapshot: executions.append(
        (pending, selected, snapshot, algorithm._peak_equity)
    )
    algorithm._record_close = lambda selected, target: closes.append((selected, target))

    algorithm.on_data(Data())

    assert security.marks == [98.0, 97.0]
    assert len(executions) == 1
    _, selected_bar, snapshot, peak_at_pending = executions[0]
    assert selected_bar is bar
    assert snapshot.execution_timestamp == bar.timestamp
    assert snapshot.reference_open == bar.open
    assert snapshot.current_equity == Decimal("99421.00480000")
    assert peak_at_pending == snapshot.peak_equity
    assert closes == [(bar, Decimal("0"))]
    assert algorithm._bar_index == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cash", Decimal("-1")),
        ("cash", Decimal("NaN")),
        ("quantity", Decimal("-1")),
        ("quantity", Decimal("0.5")),
        ("quantity", Decimal("Infinity")),
        ("open_price", Decimal("0")),
        ("open_price", Decimal("NaN")),
        ("previous_close_equity", Decimal("0")),
        ("previous_close_equity", Decimal("Infinity")),
        ("previous_peak_equity", Decimal("0")),
        ("previous_peak_equity", Decimal("NaN")),
    ],
)
def test_parity_open_phase_snapshot_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Decimal,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    inputs = {
        "cash": Decimal("90013.0048"),
        "quantity": Decimal("96"),
        "open_price": Decimal("98"),
        "previous_close_equity": Decimal("99517.0048"),
        "previous_peak_equity": Decimal("100000"),
    }
    inputs[field] = value

    with pytest.raises(ValueError):
        _open_snapshot(module, **inputs)

    with pytest.raises(ValueError, match="current equity"):
        _open_snapshot(
            module,
            cash=Decimal("0"),
            quantity=Decimal("0"),
        )
    snapshot = _open_snapshot(module)
    for notional in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="execution notional"):
            snapshot.order_weight(notional)


def test_parity_open_phase_regression_recreates_close_contamination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    tolerance = Decimal(load_contract_bundle().contract["tolerances"]["ratio"])
    notional = Decimal("9406.1184")
    open_snapshot = _open_snapshot(module, open_price=Decimal("98"))
    close_snapshot = _open_snapshot(module, open_price=Decimal("97"))

    expected = {
        "daily_loss_pct": Decimal("0.0009646592598476513"),
        "drawdown_pct": Decimal("0.005789953996800032"),
        "order_weight": Decimal("0.0946089655582672"),
    }
    observed = {
        "daily_loss_pct": Decimal("0.001929318515824141845555223141"),
        "drawdown_pct": Decimal("0.006749952"),
        "order_weight": Decimal("0.09470040720300070904199946235"),
    }
    open_metrics = {
        "daily_loss_pct": open_snapshot.daily_loss_pct,
        "drawdown_pct": open_snapshot.drawdown_pct,
        "order_weight": open_snapshot.order_weight(notional),
    }
    close_metrics = {
        "daily_loss_pct": close_snapshot.daily_loss_pct,
        "drawdown_pct": close_snapshot.drawdown_pct,
        "order_weight": close_snapshot.order_weight(notional),
    }

    assert all(abs(open_metrics[field] - value) <= tolerance for field, value in expected.items())
    assert close_metrics == observed
    assert all(abs(close_metrics[field] - expected[field]) > tolerance for field in expected)


def test_parity_risk_decision_keeps_buy_exit_and_projected_state_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    buy_timestamp = "2024-01-05T00:00:00+00:00"
    buy_snapshot = _open_snapshot(
        module,
        timestamp=buy_timestamp,
        open_price=Decimal("104"),
        cash=Decimal("100000"),
        quantity=Decimal("0"),
        previous_close_equity=Decimal("100000"),
        previous_peak_equity=Decimal("100000"),
    )
    buy_algorithm = _risk_algorithm(module, timestamp=buy_timestamp)
    buy_risk, buy_reasons = buy_algorithm._risk_decision(
        intent_index=0,
        quantity=Decimal("96"),
        reference_open=Decimal("104"),
        estimated_price=Decimal("104.0208"),
        estimated_fee=Decimal("0.99859968"),
        target_weight=Decimal("0.1"),
        open_snapshot=buy_snapshot,
    )

    assert buy_reasons == ()
    assert buy_risk["status"] == "approved"
    assert buy_risk["metrics"] == {
        "asset_weight": "0.09984299069662382909994766358",
        "daily_loss_pct": "0",
        "drawdown_pct": "0",
        "order_weight": "0.099859968",
        "projected_cash": "90013.00460032",
        "projected_equity": "99997.00460032",
        "reduces_risk": "0",
        "total_gross_weight": "0.09984299069662382909994766358",
    }

    exit_snapshot = _open_snapshot(module)
    exit_algorithm = _risk_algorithm(
        module,
        timestamp="2024-01-09T00:00:00+00:00",
        prior_decision_count=1,
    )
    exit_risk, exit_reasons = exit_algorithm._risk_decision(
        intent_index=1,
        quantity=Decimal("-96"),
        reference_open=Decimal("98"),
        estimated_price=Decimal("97.9804"),
        estimated_fee=Decimal("0.94061184"),
        target_weight=Decimal("0"),
        open_snapshot=exit_snapshot,
    )

    assert exit_reasons == ()
    assert exit_risk["status"] == "approved"
    assert exit_risk["metrics"]["reduces_risk"] == "1"
    assert exit_risk["metrics"]["projected_cash"] == "99418.18258816"
    assert exit_risk["metrics"]["projected_equity"] == "99418.18258816"
    assert _source(PARITY).count("self.portfolio.total_portfolio_value") == 1


def test_corrected_open_snapshot_passes_real_comparator_without_tolerance_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    local = build_local_parity_trace()
    candidate = copy.deepcopy(local)
    candidate["provenance"] = "contract_fixture_not_engine_observation"
    candidate["engine"] = {
        "name": "lean_contract_fixture",
        "version": "not_observed_from_lean",
    }

    buy_timestamp = "2024-01-05T00:00:00+00:00"
    buy_algorithm = _risk_algorithm(module, timestamp=buy_timestamp)
    buy_risk, _ = buy_algorithm._risk_decision(
        intent_index=0,
        quantity=Decimal("96"),
        reference_open=Decimal("104"),
        estimated_price=Decimal("104.0208"),
        estimated_fee=Decimal("0.99859968"),
        target_weight=Decimal("0.1"),
        open_snapshot=_open_snapshot(
            module,
            timestamp=buy_timestamp,
            open_price=Decimal("104"),
            cash=Decimal("100000"),
            quantity=Decimal("0"),
            previous_close_equity=Decimal("100000"),
            previous_peak_equity=Decimal("100000"),
        ),
    )
    exit_algorithm = _risk_algorithm(
        module,
        timestamp="2024-01-09T00:00:00+00:00",
        prior_decision_count=1,
    )
    exit_risk, _ = exit_algorithm._risk_decision(
        intent_index=1,
        quantity=Decimal("-96"),
        reference_open=Decimal("98"),
        estimated_price=Decimal("97.9804"),
        estimated_fee=Decimal("0.94061184"),
        target_weight=Decimal("0"),
        open_snapshot=_open_snapshot(module),
    )
    candidate["risk_decisions"] = [buy_risk, exit_risk]

    comparison = compare_parity_traces(local, candidate)
    assert comparison.matched
    assert comparison.dimensions == {dimension: "matched" for dimension in COMPARISON_DIMENSIONS}
    assert comparison.tolerances["ratio"] == "0.0000001"
    assert comparison.tolerances["quantity"] == "0"

    close_candidate = copy.deepcopy(candidate)
    close_snapshot = _open_snapshot(module, open_price=Decimal("97"))
    close_candidate["risk_decisions"][1]["metrics"].update(
        daily_loss_pct=module.canonical_decimal(close_snapshot.daily_loss_pct),
        drawdown_pct=module.canonical_decimal(close_snapshot.drawdown_pct),
        order_weight=module.canonical_decimal(close_snapshot.order_weight(Decimal("9406.1184"))),
    )
    with pytest.raises(ParityMismatchError) as mismatch:
        compare_parity_traces(local, close_candidate)
    assert set(mismatch.value.differences_by_dimension) == {"rejection_and_halt_state"}
    differences = mismatch.value.differences_by_dimension["rejection_and_halt_state"]
    assert len(differences) == 3
    assert all(
        any(field in difference for difference in differences)
        for field in ("daily_loss_pct", "drawdown_pct", "order_weight")
    )

    contract_tolerances = load_contract_bundle().contract["tolerances"]
    assert contract_tolerances["ratio"] == "0.0000001"
    assert contract_tolerances["quantity"] == "0"


def test_parity_observation_serialization_is_stable_bounded_and_prefixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_parity_with_algorithm_stubs(monkeypatch)
    trace = {
        "engine": {"version": "2.5.0", "name": "quantconnect_lean"},
        "schema_version": "1.0.0",
    }

    first = module.canonical_trace_line(trace)
    second = module.canonical_trace_line(dict(reversed(list(trace.items()))))
    assert first == second
    assert first.startswith("TRADING_BOT_LAB_LEAN_PARITY_V1:{")
    assert "\n" not in first and "\r" not in first
    assert "quantconnect_lean" in first
    with pytest.raises(ValueError):
        module.canonical_trace_line({"bad": float("nan")})
