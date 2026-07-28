from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite

from AlgorithmImports import (
    AccountType,
    BrokerageName,
    CashAmount,
    ConstantSlippageModel,
    DataNormalizationMode,
    FeeModel,
    Globals,
    OrderFee,
    OrderStatus,
    QCAlgorithm,
    Resolution,
    Slice,
    TimeZones,
)


OBSERVATION_PREFIX = "TRADING_BOT_LAB_LEAN_WALK_FORWARD_V1:"
MAX_OBSERVATION_PAYLOAD_BYTES = 16_384
SCHEMA_VERSION = "1.0.0"
PROTOCOL_VERSION = "1.0.0"
ENGINE_NAME = "quantconnect_lean"
ENGINE_VERSION_PATTERN = re.compile(r"^[0-9]{1,9}(?:\.[0-9]{1,9}){1,5}$")
PROJECT_SOURCE_SHA256 = "bfa754090510c26c228e8bbd115b7d9bf49279f255d03f180585f978a94ac20c"
PUBLIC_CONFIGURATION_SHA256 = "034571b4a1a8406ced12ba2d9ccd8d62449ade3c8d231cdc3b06c036f9089fff"

SYMBOL = "SPY"
INITIAL_CASH = 100_000.0
FAST_PERIOD = 20
SLOW_PERIOD = 50
WARMUP_BARS = 50
TARGET_WEIGHT = 0.10
MAX_POSITION_WEIGHT = 0.10
MAX_TOTAL_EXPOSURE = 0.30
FEE_BPS = 1.0
MINIMUM_FEE = 1.0
SLIPPAGE_BPS = 2.0
MAX_DAILY_LOSS = 0.02
MAX_DRAWDOWN = 0.05

FOLD_WINDOWS = {
    "spy-2021": (date(2021, 1, 1), date(2021, 12, 31)),
    "spy-2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "spy-2023": (date(2023, 1, 1), date(2023, 12, 31)),
    "spy-2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "spy-2025": (date(2025, 1, 1), date(2025, 12, 31)),
}

# These names are deliberately unsupported and covered by full parameter-set validation.
FORBIDDEN_OVERRIDE_PARAMETERS = (
    "account-type",
    "automatic-liquidation",
    "brokerage",
    "data-normalization",
    "data-normalization-mode",
    "end-date",
    "fast-period",
    "fee-bps",
    "fill-forward",
    "initial-cash",
    "leverage",
    "live-mode",
    "max-daily-loss",
    "max-drawdown",
    "max-gross-exposure",
    "max-order-notional-weight",
    "max-position-weight",
    "max-total-exposure",
    "minimum-fee",
    "optimization-id",
    "optimizer",
    "project-source-sha256",
    "public-configuration-sha256",
    "resolution",
    "slippage-bps",
    "slow-period",
    "start-date",
    "symbol",
    "target-weight",
    "warmup-bars",
)


def canonical_decimal(value: object) -> str:
    """Return a finite exponent-free decimal string for public observations."""

    if isinstance(value, bool):
        raise ValueError("observation decimal cannot be a boolean")
    try:
        selected = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("observation decimal must be numeric") from exc
    if not selected.is_finite():
        raise ValueError("observation decimal must be finite")
    if selected == 0:
        return "0"
    rendered = format(selected.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_utc_timestamp(value: datetime) -> str:
    """Render a LEAN algorithm timestamp as canonical whole-second UTC."""

    if not isinstance(value, datetime):
        raise ValueError("evaluation timestamp must be a datetime")
    selected = value
    if selected.tzinfo is not None:
        selected = selected.astimezone(UTC).replace(tzinfo=None)
    return selected.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_observation_line(observation: dict[str, object]) -> str:
    """Serialize one size-bounded compact canonical observation."""

    encoded = json.dumps(
        observation,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if "\n" in encoded or "\r" in encoded:
        raise ValueError("walk-forward observation must occupy one logical line")
    if len(encoded.encode("utf-8")) > MAX_OBSERVATION_PAYLOAD_BYTES:
        raise ValueError("walk-forward observation exceeds its fixed byte bound")
    return OBSERVATION_PREFIX + encoded


def compute_bps_minimum_fee(notional: float) -> float:
    """Return the fixed v1 fee for one non-zero order."""

    if not isfinite(notional) or notional <= 0:
        raise ValueError("notional must be positive and finite")
    return max(notional * FEE_BPS / 10_000.0, MINIMUM_FEE)


class MovingAverageSignalModel:
    """Fixed trailing completed-close signal component."""

    fast_period = FAST_PERIOD
    slow_period = SLOW_PERIOD
    target_weight = TARGET_WEIGHT

    def target_for_completed_closes(self, closes: tuple[float, ...]) -> float | None:
        if len(closes) < self.slow_period:
            return None
        fast = sum(closes[-self.fast_period :]) / self.fast_period
        slow = sum(closes[-self.slow_period :]) / self.slow_period
        return self.target_weight if fast > slow else 0.0


class LongOnlyPortfolioModel:
    """Validate the fixed signal against independent long-only caps."""

    def validate_target(self, target_weight: float) -> float:
        if not isfinite(target_weight) or target_weight < 0:
            raise ValueError("short or invalid targets are forbidden")
        if target_weight > MAX_POSITION_WEIGHT:
            raise ValueError("target exceeds max position weight")
        if target_weight > MAX_TOTAL_EXPOSURE:
            raise ValueError("target exceeds max total exposure")
        return target_weight


class LatchedRiskModel:
    """Track inclusive daily-loss and peak-drawdown guards without liquidation."""

    def __init__(self) -> None:
        self._session_key = None
        self._start_of_day_equity = INITIAL_CASH
        self._last_close_equity = INITIAL_CASH
        self._peak_equity = INITIAL_CASH
        self._halt_reasons: tuple[str, ...] = ()

    @property
    def halted(self) -> bool:
        return bool(self._halt_reasons)

    @property
    def halt_reasons(self) -> tuple[str, ...]:
        return self._halt_reasons

    def observe(self, session_key: object, equity: float) -> tuple[str, ...]:
        if self.halted:
            return self._halt_reasons
        if not isfinite(equity) or equity <= 0:
            self._halt_reasons = ("invalid_equity",)
            return self._halt_reasons
        if self._session_key != session_key:
            self._session_key = session_key
            self._start_of_day_equity = self._last_close_equity

        self._peak_equity = max(self._peak_equity, equity)
        daily_loss = max(0.0, self._start_of_day_equity - equity)
        daily_loss /= self._start_of_day_equity
        drawdown = max(0.0, self._peak_equity - equity) / self._peak_equity

        reasons: list[str] = []
        if daily_loss >= MAX_DAILY_LOSS:
            reasons.append("daily_loss")
        if drawdown >= MAX_DRAWDOWN:
            reasons.append("max_drawdown")
        if reasons:
            self._halt_reasons = tuple(reasons)
        return self._halt_reasons

    def close_session(self, session_key: object, equity: float) -> tuple[str, ...]:
        reasons = self.observe(session_key, equity)
        self._last_close_equity = equity
        return reasons


class BpsMinimumFeeModel(FeeModel):
    """Charge the fixed v1 notional-bps fee with a per-order minimum."""

    def get_order_fee(self, parameters) -> OrderFee:
        notional = abs(float(parameters.security.price) * float(parameters.order.absolute_quantity))
        fee = compute_bps_minimum_fee(notional)
        currency = parameters.security.quote_currency.symbol
        return OrderFee(CashAmount(fee, currency))


class NextOpenExecutionModel:
    """Translate approved targets exclusively into market-on-open orders."""

    def __init__(self, symbol) -> None:
        self._symbol = symbol

    def cancel_open_orders(self, algorithm: QCAlgorithm, reason: str) -> None:
        algorithm.transactions.cancel_open_orders(self._symbol, reason)

    def submit_target(self, algorithm: QCAlgorithm, target_weight: float, tag: str) -> bool:
        if algorithm.transactions.get_open_orders(self._symbol):
            return False
        quantity = float(algorithm.calculate_order_quantity(self._symbol, target_weight))
        current_quantity = float(algorithm.portfolio[self._symbol].quantity)
        if current_quantity + quantity < -1e-9:
            raise RuntimeError("target translation would create a short position")
        if abs(quantity) <= 1e-9:
            return False
        algorithm.market_on_open_order(self._symbol, quantity, tag=tag)
        return True


class WalkForwardMovingAverageV1(QCAlgorithm):
    """Fixed five-fold research-only SPY walk-forward validation algorithm."""

    def initialize(self) -> None:
        if self.live_mode:
            raise RuntimeError(
                "WalkForwardMovingAverageV1 is backtest-only; live mode is forbidden"
            )

        parameters = {str(name): str(value) for name, value in self.get_parameters().items()}
        allowed_parameter_names = {"fold-id", "optimization-mode"}
        if set(FORBIDDEN_OVERRIDE_PARAMETERS).intersection(parameters):
            raise RuntimeError("unsupported walk-forward parameter override")
        if set(parameters) - allowed_parameter_names:
            raise RuntimeError("unsupported walk-forward parameter")
        if parameters.get("optimization-mode", "false") != "false":
            raise RuntimeError("optimization-mode must remain exactly false")

        fold_id = parameters.get("fold-id", "")
        if fold_id not in FOLD_WINDOWS:
            raise ValueError("fold-id must name one of the five predeclared walk-forward v1 folds")
        if set(parameters) != allowed_parameter_names:
            raise RuntimeError(
                "walk-forward parameter set must contain fold-id and optimization-mode"
            )
        evaluation_start, evaluation_end = FOLD_WINDOWS[fold_id]

        self._fold_id = fold_id
        self._evaluation_start = evaluation_start
        self._evaluation_end = evaluation_end
        self._signal_model = MovingAverageSignalModel()
        self._portfolio_model = LongOnlyPortfolioModel()
        self._risk_model = LatchedRiskModel()
        self._completed_closes: list[float] = []
        self._warmup_completed = False
        self._first_eligible_timestamp: str | None = None
        self._last_processed_timestamp: str | None = None
        self._starting_equity: float | None = None
        self._benchmark_starting_value: float | None = None
        self._benchmark_ending_value: float | None = None
        self._metric_peak_equity = INITIAL_CASH
        self._maximum_drawdown = 0.0
        self._estimated_slippage = 0.0
        self._total_fees = 0.0
        self._order_count = 0
        self._fill_count = 0
        self._rejected_order_count = 0
        self._observation_emitted = False
        self._final_evaluation_close_seen = False

        self.set_start_date(evaluation_start.year, evaluation_start.month, evaluation_start.day)
        self.set_end_date(evaluation_end.year, evaluation_end.month, evaluation_end.day)
        self.set_time_zone(TimeZones.UTC)
        # Pin daily emissions to the completed exchange close on the same UTC date.
        # This maps the public closed interval directly to LEAN's inclusive dates.
        self.settings.daily_precise_end_time = True
        self.set_cash(INITIAL_CASH)
        self.set_brokerage_model(
            BrokerageName.QUANT_CONNECT_BROKERAGE,
            AccountType.CASH,
        )

        security = self.add_equity(
            SYMBOL,
            Resolution.DAILY,
            fill_forward=False,
            data_normalization_mode=DataNormalizationMode.ADJUSTED,
        )
        security.set_leverage(1.0)
        security.set_fee_model(BpsMinimumFeeModel())
        security.set_slippage_model(ConstantSlippageModel(SLIPPAGE_BPS / 10_000.0))
        self._security = security
        self._symbol = security.symbol
        self._execution_model = NextOpenExecutionModel(self._symbol)
        self.set_benchmark(self._symbol)
        self.set_warm_up(WARMUP_BARS, Resolution.DAILY)

    def on_warmup_finished(self) -> None:
        if len(self._completed_closes) < WARMUP_BARS:
            raise RuntimeError("walk-forward v1 requires all 50 completed warmup bars")
        self._warmup_completed = True

    def on_data(self, data: Slice) -> None:
        if not data.contains_key(self._symbol) or data[self._symbol] is None:
            return
        bar = data[self._symbol]
        if bar.is_fill_forward:
            return

        close = float(bar.close)
        if not isfinite(close) or close <= 0:
            raise RuntimeError("SPY close must be positive and finite")
        self._completed_closes.append(close)
        if len(self._completed_closes) > SLOW_PERIOD:
            self._completed_closes.pop(0)

        if self.is_warming_up:
            return
        if not self._warmup_completed:
            raise RuntimeError("evaluation data arrived before warmup completion was confirmed")
        current_date = self.time.date()
        if current_date < self._evaluation_start or current_date > self._evaluation_end:
            raise RuntimeError("evaluation bar falls outside the closed fold interval")

        timestamp = canonical_utc_timestamp(self.time)
        equity = float(self.portfolio.total_portfolio_value)
        if self._first_eligible_timestamp is None:
            if not isfinite(equity) or equity <= 0:
                raise RuntimeError("starting evaluation equity must be positive and finite")
            self._first_eligible_timestamp = timestamp
            self._starting_equity = equity
            self._benchmark_starting_value = close
            self._metric_peak_equity = equity
        self._last_processed_timestamp = timestamp
        self._benchmark_ending_value = close
        self._observe_metric_equity(equity)
        next_open_within_evaluation = self._next_open_is_within_evaluation()
        if not next_open_within_evaluation:
            self._final_evaluation_close_seen = True

        reasons = self._risk_model.close_session(current_date, equity)
        if reasons:
            self._execution_model.cancel_open_orders(self, "walk-forward v1 risk halt latched")
            return

        signal_target = self._signal_model.target_for_completed_closes(
            tuple(self._completed_closes)
        )
        if signal_target is None:
            raise RuntimeError("fixed warmup completed without a ready slow moving-average window")
        target = self._portfolio_model.validate_target(signal_target)
        if not next_open_within_evaluation:
            return
        if self._execution_model.submit_target(
            self,
            target,
            "walk-forward v1 completed-close signal; next-session MOO",
        ):
            self._order_count += 1

    def _next_open_is_within_evaluation(self) -> bool:
        next_open = self._security.exchange.hours.get_next_market_open(self.time, False)
        return next_open.date() <= self._evaluation_end

    def on_order_event(self, order_event) -> None:
        if order_event.status == OrderStatus.INVALID:
            self._rejected_order_count += 1
            return
        if order_event.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return

        self._fill_count += 1
        fill_quantity = float(order_event.fill_quantity)
        fill_price = float(order_event.fill_price)
        if not isfinite(fill_quantity) or not isfinite(fill_price) or fill_price <= 0:
            raise RuntimeError("LEAN emitted an invalid walk-forward fill")
        if abs(fill_quantity) > 0:
            slippage_fraction = SLIPPAGE_BPS / 10_000.0
            multiplier = 1.0 + slippage_fraction if fill_quantity > 0 else 1.0 - slippage_fraction
            reference_price = fill_price / multiplier
            self._estimated_slippage += abs(fill_quantity) * abs(fill_price - reference_price)

        fee = float(order_event.order_fee.value.amount)
        if not isfinite(fee) or fee < 0:
            raise RuntimeError("LEAN emitted an invalid walk-forward order fee")
        self._total_fees += fee

        equity = float(self.portfolio.total_portfolio_value)
        if self._first_eligible_timestamp is not None:
            self._observe_metric_equity(equity)
        reasons = self._risk_model.observe(self.time.date(), equity)
        if reasons:
            self._execution_model.cancel_open_orders(self, "walk-forward v1 risk halt latched")

    def _observe_metric_equity(self, equity: float) -> None:
        if not isfinite(equity) or equity <= 0:
            raise RuntimeError("evaluation equity must be positive and finite")
        self._metric_peak_equity = max(self._metric_peak_equity, equity)
        drawdown = max(0.0, self._metric_peak_equity - equity) / self._metric_peak_equity
        self._maximum_drawdown = max(self._maximum_drawdown, drawdown)

    def on_end_of_algorithm(self) -> None:
        self._execution_model.cancel_open_orders(
            self,
            "walk-forward evaluation ended; cancel pending MOO without fabricating a fill",
        )
        if self._observation_emitted:
            raise RuntimeError("walk-forward v1 observation was already emitted")
        if (
            not self._warmup_completed
            or self._first_eligible_timestamp is None
            or self._last_processed_timestamp is None
            or self._starting_equity is None
            or self._benchmark_starting_value is None
            or self._benchmark_ending_value is None
            or not self._final_evaluation_close_seen
        ):
            raise RuntimeError("walk-forward fold ended without complete evaluation state")
        engine_version = str(Globals.version)
        if ENGINE_VERSION_PATTERN.fullmatch(engine_version) is None:
            raise RuntimeError("LEAN runtime version must be a safe dotted numeric value")

        ending_equity = float(self.portfolio.total_portfolio_value)
        self._observe_metric_equity(ending_equity)
        quantity = float(self.portfolio[self._symbol].quantity)
        if not isfinite(quantity) or quantity < -1e-9:
            raise RuntimeError("walk-forward fold ended with an invalid or short position")
        quantity = max(0.0, quantity)
        starting_equity_decimal = Decimal(str(self._starting_equity))
        ending_equity_decimal = Decimal(str(ending_equity))
        benchmark_start_decimal = Decimal(str(self._benchmark_starting_value))
        benchmark_end_decimal = Decimal(str(self._benchmark_ending_value))
        total_return = ending_equity_decimal / starting_equity_decimal - Decimal("1")
        benchmark_return = benchmark_end_decimal / benchmark_start_decimal - Decimal("1")

        observation: dict[str, object] = {
            "costs": {
                "fee_bps": "1.0",
                "minimum_fee_usd": "1.0",
                "slippage_bps": "2.0",
            },
            "data": {
                "data_normalization": "adjusted",
                "resolution": "daily",
                "symbol": SYMBOL,
            },
            "engine": {
                "name": ENGINE_NAME,
                "version": engine_version,
            },
            "evaluation_end": self._evaluation_end.isoformat(),
            "evaluation_start": self._evaluation_start.isoformat(),
            "execution": {
                "fill_forward": False,
                "final_signal_expires_without_next_open": True,
                "first_eligible_signal": "completed_trailing_history_only",
                "orders_during_warmup": False,
                "signal_time": "completed_daily_close",
                "timing": "next_market_open",
            },
            "fold_id": self._fold_id,
            "metrics": {
                "benchmark_ending_value": canonical_decimal(self._benchmark_ending_value),
                "benchmark_return": canonical_decimal(benchmark_return),
                "benchmark_starting_value": canonical_decimal(self._benchmark_starting_value),
                "ending_equity_usd": canonical_decimal(ending_equity),
                "estimated_slippage_usd": canonical_decimal(self._estimated_slippage),
                "excess_return": canonical_decimal(total_return - benchmark_return),
                "fill_count": self._fill_count,
                "maximum_drawdown": canonical_decimal(self._maximum_drawdown),
                "order_count": self._order_count,
                "rejected_order_count": self._rejected_order_count,
                "starting_equity_usd": canonical_decimal(self._starting_equity),
                "total_fees_usd": canonical_decimal(self._total_fees),
                "total_return": canonical_decimal(total_return),
            },
            "protocol_version": PROTOCOL_VERSION,
            "risk": {
                "account_type": "cash",
                "automatic_liquidation": False,
                "leverage": "1",
                "long_only": True,
                "max_daily_loss": "0.02",
                "max_drawdown": "0.05",
                "max_gross_exposure": "0.30",
                "max_position_weight": "0.10",
            },
            "schema_version": SCHEMA_VERSION,
            "source": {
                "project_source_sha256": PROJECT_SOURCE_SHA256,
                "public_configuration_sha256": PUBLIC_CONFIGURATION_SHA256,
            },
            "state": {
                "completion_status": "completed",
                "final_evaluation_close_seen": self._final_evaluation_close_seen,
                "final_position": {
                    "quantity": canonical_decimal(quantity),
                    "state": "long" if quantity > 1e-9 else "cash",
                },
                "first_eligible_evaluation_timestamp": self._first_eligible_timestamp,
                "halt_reasons": list(self._risk_model.halt_reasons),
                "last_processed_evaluation_timestamp": self._last_processed_timestamp,
                "risk_halted": self._risk_model.halted,
                "warmup_completed": self._warmup_completed,
            },
            "strategy": {
                "fast_period": FAST_PERIOD,
                "slow_period": SLOW_PERIOD,
                "target_weight": "0.10",
                "warmup_bars": WARMUP_BARS,
            },
        }
        self._observation_emitted = True
        self.debug(canonical_observation_line(observation))
