from __future__ import annotations

import ast
import importlib.util
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from trading_bot_lab.walk_forward.contract import (
    FOLDS,
    load_protocol_bundle,
    project_source_sha256,
    public_configuration_sha256,
)
from trading_bot_lab.walk_forward.observation import normalize_observation

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "lean-workspace" / "Strategies" / "WalkForwardMovingAverageV1"
BASELINE_CONFIG = ROOT / "lean-workspace" / "Strategies" / "MovingAverageBaseline" / "config.json"


def _load_project(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    algorithm_imports = ModuleType("AlgorithmImports")

    class CashAmount:
        def __init__(self, amount: float, currency: str) -> None:
            self.amount = amount
            self.currency = currency

    class OrderFee:
        def __init__(self, value: CashAmount) -> None:
            self.value = value

    class ConstantSlippageModel:
        def __init__(self, value: float) -> None:
            self.value = value

    stubs = {
        "AccountType": type("AccountType", (), {"CASH": "cash"}),
        "BrokerageName": type("BrokerageName", (), {"QUANT_CONNECT_BROKERAGE": "quant-connect"}),
        "CashAmount": CashAmount,
        "ConstantSlippageModel": ConstantSlippageModel,
        "DataNormalizationMode": type("DataNormalizationMode", (), {"ADJUSTED": "adjusted"}),
        "FeeModel": type("FeeModel", (), {}),
        "Globals": type("Globals", (), {"version": "2.5.0.0.17942"}),
        "OrderFee": OrderFee,
        "OrderStatus": type(
            "OrderStatus",
            (),
            {
                "FILLED": "filled",
                "INVALID": "invalid",
                "PARTIALLY_FILLED": "partially-filled",
            },
        ),
        "QCAlgorithm": type("QCAlgorithm", (), {}),
        "Resolution": type("Resolution", (), {"DAILY": "daily"}),
        "Slice": type("Slice", (), {}),
        "TimeZones": type("TimeZones", (), {"UTC": "utc"}),
    }
    for name, value in stubs.items():
        setattr(algorithm_imports, name, value)
    monkeypatch.setitem(sys.modules, "AlgorithmImports", algorithm_imports)

    module_name = "lean_test_WalkForwardMovingAverageV1"
    spec = importlib.util.spec_from_file_location(module_name, PROJECT / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


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


def test_project_is_public_safe_and_has_no_network_live_optimization_or_object_store_surface() -> (
    None
):
    source = (PROJECT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    config = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
    imports = {
        node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
    }

    assert config == {
        "algorithm-language": "Python",
        "description": (
            "Research-only fixed-parameter SPY walk-forward v1. Backtest only. "
            "Live and optimization modes forbidden."
        ),
        "parameters": {"fold-id": "__required__", "optimization-mode": "false"},
    }
    assert all(character not in config["description"] for character in (",", ";"))
    assert imports.isdisjoint(
        {"aiohttp", "httpx", "requests", "socket", "urllib", "websocket", "websockets"}
    )
    assert "object_store" not in source.casefold()
    assert "http://" not in source and "https://" not in source
    assert {"buy", "sell", "liquidate", "set_holdings", "market_order"}.isdisjoint(
        _call_names(source)
    )
    assert "market_on_open_order" in _call_names(source)
    assert "self.live_mode" in source
    assert "optimization-mode must remain exactly false" in source
    assert "self.settings.daily_precise_end_time = True" in source


def test_project_hashes_fold_mapping_and_inclusive_dates_match_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    bundle = load_protocol_bundle()

    assert bundle.project_source_sha256 == project_source_sha256()
    assert bundle.project_source_sha256 == module.PROJECT_SOURCE_SHA256
    assert bundle.public_configuration_sha256 == public_configuration_sha256()
    assert bundle.public_configuration_sha256 == module.PUBLIC_CONFIGURATION_SHA256
    assert (
        tuple(
            (fold_id, start.isoformat(), end.isoformat())
            for fold_id, (start, end) in module.FOLD_WINDOWS.items()
        )
        == FOLDS
    )
    assert all(start <= end for start, end in module.FOLD_WINDOWS.values())


def test_fixed_strategy_risk_cost_and_account_constants_match_the_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    baseline = json.loads(BASELINE_CONFIG.read_text(encoding="utf-8"))["parameters"]

    assert module.SYMBOL == baseline["symbol"] == "SPY"
    assert module.INITIAL_CASH == float(baseline["initial-cash"]) == 100_000.0
    assert module.FAST_PERIOD == int(baseline["fast-period"]) == 20
    assert module.SLOW_PERIOD == int(baseline["slow-period"]) == 50
    assert module.WARMUP_BARS == int(baseline["warmup-bars"]) == 50
    assert module.TARGET_WEIGHT == float(baseline["target-weight"]) == 0.10
    assert module.MAX_POSITION_WEIGHT == float(baseline["max-position-weight"]) == 0.10
    assert module.MAX_TOTAL_EXPOSURE == float(baseline["max-total-exposure"]) == 0.30
    assert module.FEE_BPS == float(baseline["fee-bps"]) == 1.0
    assert module.MINIMUM_FEE == float(baseline["minimum-fee"]) == 1.0
    assert module.SLIPPAGE_BPS == float(baseline["slippage-bps"]) == 2.0
    assert module.MAX_DAILY_LOSS == float(baseline["max-daily-loss"]) == 0.02
    assert module.MAX_DRAWDOWN == float(baseline["max-drawdown"]) == 0.05


@pytest.mark.parametrize(
    ("live_mode", "parameters", "message"),
    [
        (True, {}, "live mode is forbidden"),
        (False, {"optimization-mode": "true"}, "optimization-mode"),
        (False, {"fold-id": "spy-2026"}, "five predeclared"),
        (False, {"start-date": "2021-01-01", "fold-id": "spy-2021"}, "override"),
        (False, {"end-date": "2021-12-31", "fold-id": "spy-2021"}, "override"),
        (False, {"symbol": "QQQ", "fold-id": "spy-2021"}, "override"),
        (False, {"resolution": "hour", "fold-id": "spy-2021"}, "override"),
        (False, {"leverage": "2", "fold-id": "spy-2021"}, "override"),
        (False, {"target-weight": "0.11", "fold-id": "spy-2021"}, "override"),
        (False, {"max-drawdown": "0.06", "fold-id": "spy-2021"}, "override"),
        (False, {"fee-bps": "0", "fold-id": "spy-2021"}, "override"),
    ],
)
def test_initialize_fails_before_side_effects_for_live_optimization_unknown_fold_or_overrides(
    monkeypatch: pytest.MonkeyPatch,
    live_mode: bool,
    parameters: dict[str, str],
    message: str,
) -> None:
    module = _load_project(monkeypatch)
    algorithm = module.WalkForwardMovingAverageV1()
    algorithm.live_mode = live_mode
    algorithm.get_parameter = lambda name, default="": parameters.get(name, default)
    algorithm.get_parameters = lambda: parameters
    algorithm.set_start_date = lambda *_args: (_ for _ in ()).throw(
        AssertionError("initialization side effect occurred before rejection")
    )

    with pytest.raises((RuntimeError, ValueError), match=message):
        algorithm.initialize()


def test_initialize_uses_direct_inclusive_fold_dates_cash_daily_adjusted_one_times_leverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    calls: dict[str, Any] = {}

    class Security:
        symbol = "SPY"

        def set_leverage(self, value: float) -> None:
            calls["leverage"] = value

        def set_fee_model(self, value: object) -> None:
            calls["fee_model"] = type(value).__name__

        def set_slippage_model(self, value: object) -> None:
            calls["slippage"] = value.value

    algorithm = module.WalkForwardMovingAverageV1()
    algorithm.live_mode = False
    algorithm.settings = SimpleNamespace(daily_precise_end_time=False)
    parameters = {"fold-id": "spy-2021", "optimization-mode": "false"}
    algorithm.get_parameter = lambda name, default="": parameters.get(name, default)
    algorithm.get_parameters = lambda: parameters
    algorithm.set_start_date = lambda *value: calls.setdefault("start", value)
    algorithm.set_end_date = lambda *value: calls.setdefault("end", value)
    algorithm.set_time_zone = lambda value: calls.setdefault("timezone", value)
    algorithm.set_cash = lambda value: calls.setdefault("cash", value)
    algorithm.set_brokerage_model = lambda *value: calls.setdefault("brokerage", value)

    def add_equity(symbol: str, resolution: object, **kwargs: object) -> Security:
        calls["equity"] = (symbol, resolution, kwargs)
        return Security()

    algorithm.add_equity = add_equity
    algorithm.set_benchmark = lambda value: calls.setdefault("benchmark", value)
    algorithm.set_warm_up = lambda *value: calls.setdefault("warmup", value)
    algorithm.initialize()

    assert calls["start"] == (2021, 1, 1)
    assert calls["end"] == (2021, 12, 31)
    assert algorithm._evaluation_start == date(2021, 1, 1)
    assert algorithm._evaluation_end == date(2021, 12, 31)
    assert calls["cash"] == 100_000.0
    assert calls["brokerage"] == ("quant-connect", "cash")
    assert calls["equity"] == (
        "SPY",
        "daily",
        {"fill_forward": False, "data_normalization_mode": "adjusted"},
    )
    assert calls["leverage"] == 1.0
    assert calls["warmup"] == (50, "daily")
    assert algorithm.settings.daily_precise_end_time is True


def test_signal_is_trailing_only_fixed_20_50_and_future_rows_are_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    signal = module.MovingAverageSignalModel()
    completed = tuple(float(value) for value in range(1, 51))

    assert signal.target_for_completed_closes(completed[:-1]) is None
    observed = signal.target_for_completed_closes(completed)
    assert observed == pytest.approx(0.10)
    future = completed + (-1_000_000.0,)
    assert signal.target_for_completed_closes(completed) == observed
    assert signal.target_for_completed_closes(future) == 0.0
    assert signal.target_for_completed_closes(tuple(reversed(completed))) == 0.0


def test_warmup_never_submits_orders_or_initializes_evaluation_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    algorithm = module.WalkForwardMovingAverageV1()
    algorithm._symbol = "SPY"
    algorithm._completed_closes = []
    algorithm._warmup_completed = False
    algorithm._first_eligible_timestamp = None
    algorithm._last_processed_timestamp = None
    algorithm._starting_equity = None
    algorithm._order_count = 0
    algorithm.is_warming_up = True

    class Data:
        def contains_key(self, symbol: str) -> bool:
            return symbol == "SPY"

        def __getitem__(self, symbol: str) -> object:
            assert symbol == "SPY"
            return SimpleNamespace(close=100.0, is_fill_forward=False)

    algorithm.on_data(Data())

    assert algorithm._completed_closes == [100.0]
    assert algorithm._order_count == 0
    assert algorithm._first_eligible_timestamp is None
    assert algorithm._last_processed_timestamp is None
    assert algorithm._starting_equity is None
    algorithm._completed_closes = [100.0] * 49
    with pytest.raises(RuntimeError, match="all 50"):
        algorithm.on_warmup_finished()
    algorithm._completed_closes.append(100.0)
    algorithm.on_warmup_finished()
    assert algorithm._warmup_completed is True


def test_long_only_caps_risk_latching_and_fixed_costs_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    portfolio = module.LongOnlyPortfolioModel()
    assert portfolio.validate_target(0.0) == 0.0
    assert portfolio.validate_target(0.10) == 0.10
    for invalid in (-0.01, 0.1000001, math.nan, math.inf):
        with pytest.raises(ValueError):
            portfolio.validate_target(invalid)

    risk = module.LatchedRiskModel()
    assert risk.close_session("day-1", 100_000.0) == ()
    assert risk.observe("day-2", 97_999.0) == ("daily_loss",)
    assert risk.observe("day-3", 120_000.0) == ("daily_loss",)
    drawdown = module.LatchedRiskModel()
    assert drawdown.close_session("day-1", 110_000.0) == ()
    assert "max_drawdown" in drawdown.observe("day-2", 104_000.0)
    assert module.compute_bps_minimum_fee(1_000.0) == pytest.approx(1.0)
    assert module.compute_bps_minimum_fee(1_000_000.0) == pytest.approx(100.0)
    with pytest.raises(ValueError):
        module.compute_bps_minimum_fee(0.0)


def test_execution_uses_only_next_market_open_and_final_signal_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    orders: list[tuple[str, float, str]] = []

    class Transactions:
        def get_open_orders(self, symbol: str) -> list[object]:
            return []

    fake = SimpleNamespace(
        transactions=Transactions(),
        portfolio={"SPY": SimpleNamespace(quantity=0)},
        calculate_order_quantity=lambda _symbol, _target: 10,
        market_on_open_order=lambda symbol, quantity, tag: orders.append((symbol, quantity, tag)),
    )
    execution = module.NextOpenExecutionModel("SPY")
    assert execution.submit_target(fake, 0.10, "next-open") is True
    assert orders == [("SPY", 10.0, "next-open")]

    algorithm = module.WalkForwardMovingAverageV1()
    algorithm._evaluation_end = date(2021, 12, 31)
    algorithm.time = datetime(2021, 12, 31)
    hours = SimpleNamespace(get_next_market_open=lambda _time, _extended: datetime(2022, 1, 3))
    algorithm._security = SimpleNamespace(exchange=SimpleNamespace(hours=hours))
    assert algorithm._next_open_is_within_evaluation() is False


def test_on_data_proves_the_final_close_and_risk_halts_without_liquidating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)

    class Data:
        def contains_key(self, symbol: str) -> bool:
            return symbol == "SPY"

        def __getitem__(self, symbol: str) -> object:
            assert symbol == "SPY"
            return SimpleNamespace(close=100.0, is_fill_forward=False)

    class Portfolio:
        total_portfolio_value = 100_000.0

        def __init__(self, quantity: float = 5.0) -> None:
            self.position = SimpleNamespace(quantity=quantity)

        def __getitem__(self, _symbol: str) -> object:
            return self.position

    def configured_algorithm(
        current: datetime,
        next_open: datetime,
        risk_model: object,
        cancellations: list[str],
    ) -> object:
        algorithm = module.WalkForwardMovingAverageV1()
        algorithm._symbol = "SPY"
        algorithm._completed_closes = [100.0] * 49
        algorithm._warmup_completed = True
        algorithm._first_eligible_timestamp = None
        algorithm._last_processed_timestamp = None
        algorithm._starting_equity = None
        algorithm._benchmark_starting_value = None
        algorithm._benchmark_ending_value = None
        algorithm._metric_peak_equity = 100_000.0
        algorithm._maximum_drawdown = 0.0
        algorithm._order_count = 0
        algorithm._evaluation_start = date(2021, 1, 1)
        algorithm._evaluation_end = date(2021, 12, 31)
        algorithm._final_evaluation_close_seen = False
        algorithm._signal_model = module.MovingAverageSignalModel()
        algorithm._portfolio_model = module.LongOnlyPortfolioModel()
        algorithm._risk_model = risk_model
        algorithm._execution_model = SimpleNamespace(
            cancel_open_orders=lambda _algorithm, reason: cancellations.append(reason),
            submit_target=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("final-close and risk-halt branches must not submit")
            ),
        )
        hours = SimpleNamespace(get_next_market_open=lambda _time, _extended: next_open)
        algorithm._security = SimpleNamespace(exchange=SimpleNamespace(hours=hours))
        algorithm.portfolio = Portfolio()
        algorithm.is_warming_up = False
        algorithm.time = current
        return algorithm

    final_cancellations: list[str] = []
    final = configured_algorithm(
        datetime(2021, 12, 31, 21),
        datetime(2022, 1, 3, 14, 30),
        module.LatchedRiskModel(),
        final_cancellations,
    )
    final.on_data(Data())
    assert final._final_evaluation_close_seen is True
    assert final_cancellations == []

    halt_cancellations: list[str] = []
    halted_risk = SimpleNamespace(close_session=lambda _session, _equity: ("daily_loss",))
    halted = configured_algorithm(
        datetime(2021, 1, 4, 21),
        datetime(2021, 1, 5, 14, 30),
        halted_risk,
        halt_cancellations,
    )
    starting_quantity = halted.portfolio["SPY"].quantity
    halted.on_data(Data())
    assert halt_cancellations == ["walk-forward v1 risk halt latched"]
    assert halted.portfolio["SPY"].quantity == starting_quantity


def test_end_of_fold_cancels_pending_order_emits_once_and_never_fabricates_a_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    bundle = load_protocol_bundle()
    debug_lines: list[str] = []
    cancellations: list[str] = []

    class Portfolio:
        total_portfolio_value = 100_000.0

        def __getitem__(self, _symbol: str) -> object:
            return SimpleNamespace(quantity=0.0)

    algorithm = module.WalkForwardMovingAverageV1()
    algorithm._execution_model = SimpleNamespace(
        cancel_open_orders=lambda _algorithm, reason: cancellations.append(reason)
    )
    algorithm._observation_emitted = False
    algorithm._warmup_completed = True
    algorithm._first_eligible_timestamp = "2021-01-01T00:00:00Z"
    algorithm._last_processed_timestamp = "2021-12-31T00:00:00Z"
    algorithm._starting_equity = 100_000.0
    algorithm._benchmark_starting_value = 100.0
    algorithm._benchmark_ending_value = 100.0
    algorithm._metric_peak_equity = 100_000.0
    algorithm._maximum_drawdown = 0.0
    algorithm._estimated_slippage = 0.0
    algorithm._total_fees = 0.0
    algorithm._order_count = 0
    algorithm._fill_count = 0
    algorithm._rejected_order_count = 0
    algorithm._risk_model = module.LatchedRiskModel()
    algorithm._final_evaluation_close_seen = False
    algorithm._evaluation_start = date(2021, 1, 1)
    algorithm._evaluation_end = date(2021, 12, 31)
    algorithm._fold_id = "spy-2021"
    algorithm._symbol = "SPY"
    algorithm.portfolio = Portfolio()
    algorithm.debug = debug_lines.append

    with pytest.raises(RuntimeError, match="complete evaluation state"):
        algorithm.on_end_of_algorithm()
    assert debug_lines == []

    algorithm._final_evaluation_close_seen = True
    algorithm.on_end_of_algorithm()

    assert algorithm._fill_count == 0
    assert len(debug_lines) == 1
    assert debug_lines[0].startswith(module.OBSERVATION_PREFIX)
    observation = json.loads(debug_lines[0].removeprefix(module.OBSERVATION_PREFIX))
    assert observation["metrics"]["fill_count"] == 0
    assert observation["state"]["final_evaluation_close_seen"] is True
    assert normalize_observation(observation, bundle=bundle) == observation
    assert cancellations == [
        "walk-forward evaluation ended; cancel pending MOO without fabricating a fill",
        "walk-forward evaluation ended; cancel pending MOO without fabricating a fill",
    ]
    with pytest.raises(RuntimeError, match="already emitted"):
        algorithm.on_end_of_algorithm()


def test_observation_line_is_canonical_single_line_and_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_project(monkeypatch)
    line = module.canonical_observation_line({"z": "last", "a": "first"})

    assert line == module.OBSERVATION_PREFIX + '{"a":"first","z":"last"}'
    assert "\n" not in line and "\r" not in line
    with pytest.raises(ValueError, match="byte bound"):
        module.canonical_observation_line({"value": "x" * module.MAX_OBSERVATION_PAYLOAD_BYTES})
    with pytest.raises(ValueError):
        module.canonical_observation_line({"value": math.nan})


@pytest.mark.parametrize(
    "unexpected",
    [
        "mystery-parameter",
        "project-name",
        "alternate-fold",
        "FAST_PERIOD",
        "fold-id-extra",
    ],
)
def test_initialize_rejects_every_parameter_outside_the_exact_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    unexpected: str,
) -> None:
    module = _load_project(monkeypatch)
    parameters = {
        "fold-id": "spy-2021",
        "optimization-mode": "false",
        unexpected: "operator-value",
    }
    algorithm = module.WalkForwardMovingAverageV1()
    algorithm.live_mode = False
    algorithm.get_parameters = lambda: parameters
    algorithm.get_parameter = lambda name, default="": parameters.get(name, default)
    algorithm.set_start_date = lambda *_args: (_ for _ in ()).throw(
        AssertionError("unexpected parameters must fail before initialization side effects")
    )

    with pytest.raises(RuntimeError, match="unsupported walk-forward parameter"):
        algorithm.initialize()
