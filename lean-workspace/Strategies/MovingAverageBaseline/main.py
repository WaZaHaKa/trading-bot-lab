from __future__ import annotations

from datetime import date
from math import isfinite

from AlgorithmImports import (
    AccountType,
    BrokerageName,
    CashAmount,
    ConstantSlippageModel,
    DataNormalizationMode,
    FeeModel,
    OrderFee,
    OrderStatus,
    QCAlgorithm,
    Resolution,
    Slice,
    TimeZones,
)


def parse_iso_date(raw: str, name: str) -> date:
    """Parse a strict ISO date without locale-dependent behavior."""

    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def parse_positive_int(raw: str, name: str) -> int:
    """Parse a strictly positive base-10 integer."""

    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if str(value) != str(raw).strip() or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def parse_nonnegative_int(raw: str, name: str) -> int:
    """Parse a base-10 integer that may be zero."""

    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if str(value) != str(raw).strip() or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def parse_positive_float(raw: str, name: str, maximum: float | None = None) -> float:
    """Parse a positive finite number with an optional hard upper bound."""

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} cannot exceed {maximum}")
    return value


def parse_nonnegative_float(raw: str, name: str, maximum: float | None = None) -> float:
    """Parse a non-negative finite number with an optional hard upper bound."""

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} cannot exceed {maximum}")
    return value


def parse_equity_ticker(raw: str) -> str:
    """Validate a project parameter before passing it to LEAN's equity API."""

    value = str(raw).strip().upper()
    if not value or len(value) > 16:
        raise ValueError("symbol must contain between 1 and 16 characters")
    if any(not (character.isalnum() or character in ".-") for character in value):
        raise ValueError("symbol may contain only letters, numbers, dots, and hyphens")
    return value


def compute_bps_minimum_fee(notional: float, fee_bps: float, minimum_fee: float) -> float:
    """Return max(notional bps charge, minimum fee) for a non-zero order."""

    if not isfinite(notional) or notional <= 0:
        raise ValueError("notional must be positive and finite")
    return max(notional * fee_bps / 10_000.0, minimum_fee)


class MovingAverageSignalModel:
    """Pure trailing-close signal component."""

    def __init__(self, fast_period: int, slow_period: int, target_weight: float) -> None:
        if fast_period <= 0 or slow_period <= 0 or fast_period >= slow_period:
            raise ValueError("moving-average periods require 0 < fast < slow")
        if not 0 < target_weight <= 0.10:
            raise ValueError("target_weight must be positive and no greater than 0.10")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.target_weight = target_weight

    def target_for_completed_closes(self, closes: tuple[float, ...]) -> float | None:
        if len(closes) < self.slow_period:
            return None
        fast = sum(closes[-self.fast_period :]) / self.fast_period
        slow = sum(closes[-self.slow_period :]) / self.slow_period
        return self.target_weight if fast > slow else 0.0


class LongOnlyPortfolioModel:
    """Validate a signal target against independent position and gross caps."""

    def __init__(self, max_position_weight: float, max_total_exposure: float) -> None:
        if not 0 < max_position_weight <= 0.10:
            raise ValueError("max_position_weight must be in (0, 0.10]")
        if not 0 < max_total_exposure <= 0.30:
            raise ValueError("max_total_exposure must be in (0, 0.30]")
        if max_position_weight > max_total_exposure:
            raise ValueError("position cap cannot exceed total exposure cap")
        self.max_position_weight = max_position_weight
        self.max_total_exposure = max_total_exposure

    def validate_target(self, target_weight: float) -> float:
        if not isfinite(target_weight) or target_weight < 0:
            raise ValueError("short or invalid targets are forbidden")
        if target_weight > self.max_position_weight:
            raise ValueError("target exceeds max position weight")
        if target_weight > self.max_total_exposure:
            raise ValueError("target exceeds max total exposure")
        return target_weight


class LatchedRiskModel:
    """Track daily loss and peak drawdown without resetting a breached guard."""

    def __init__(
        self,
        initial_equity: float,
        max_daily_loss: float,
        max_drawdown: float,
    ) -> None:
        if not isfinite(initial_equity) or initial_equity <= 0:
            raise ValueError("initial_equity must be positive and finite")
        if not 0 < max_daily_loss <= 0.02:
            raise ValueError("max_daily_loss must be in (0, 0.02]")
        if not 0 < max_drawdown <= 0.05:
            raise ValueError("max_drawdown must be in (0, 0.05]")
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self._session_key = None
        self._start_of_day_equity = initial_equity
        self._last_close_equity = initial_equity
        self._peak_equity = initial_equity
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
        if daily_loss >= self.max_daily_loss:
            reasons.append("daily_loss")
        if drawdown >= self.max_drawdown:
            reasons.append("max_drawdown")
        if reasons:
            self._halt_reasons = tuple(reasons)
        return self._halt_reasons

    def close_session(self, session_key: object, equity: float) -> tuple[str, ...]:
        reasons = self.observe(session_key, equity)
        self._last_close_equity = equity
        return reasons


class BpsMinimumFeeModel(FeeModel):
    """Charge a positive notional bps fee with a positive per-order minimum."""

    def __init__(self, fee_bps: float, minimum_fee: float) -> None:
        self._fee_bps = fee_bps
        self._minimum_fee = minimum_fee

    def get_order_fee(self, parameters) -> OrderFee:
        notional = abs(float(parameters.security.price) * float(parameters.order.absolute_quantity))
        fee = compute_bps_minimum_fee(notional, self._fee_bps, self._minimum_fee)
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


class MovingAverageBaseline(QCAlgorithm):
    """Backtest-only long-only SPY daily moving-average baseline."""

    def initialize(self) -> None:
        if self.live_mode:
            raise RuntimeError(
                "MovingAverageBaseline is cloud-backtest-only; live mode is forbidden"
            )

        start = parse_iso_date(self.get_parameter("start-date", "2020-01-01"), "start-date")
        end = parse_iso_date(self.get_parameter("end-date", "2021-01-01"), "end-date")
        if end <= start:
            raise ValueError("end-date must be later than start-date")

        initial_cash = parse_positive_float(
            self.get_parameter("initial-cash", "100000"),
            "initial-cash",
        )
        ticker = parse_equity_ticker(self.get_parameter("symbol", "SPY"))
        fast_period = parse_positive_int(self.get_parameter("fast-period", "20"), "fast-period")
        slow_period = parse_positive_int(self.get_parameter("slow-period", "50"), "slow-period")
        warmup_bars = parse_nonnegative_int(
            self.get_parameter("warmup-bars", "50"),
            "warmup-bars",
        )
        target_weight = parse_positive_float(
            self.get_parameter("target-weight", "0.10"),
            "target-weight",
            0.10,
        )
        max_position = parse_positive_float(
            self.get_parameter("max-position-weight", "0.10"),
            "max-position-weight",
            0.10,
        )
        max_total_exposure = parse_positive_float(
            self.get_parameter("max-total-exposure", "0.30"),
            "max-total-exposure",
            0.30,
        )
        fee_bps = parse_positive_float(self.get_parameter("fee-bps", "1.0"), "fee-bps", 100.0)
        minimum_fee = parse_nonnegative_float(
            self.get_parameter("minimum-fee", "1.0"),
            "minimum-fee",
        )
        slippage_bps = parse_positive_float(
            self.get_parameter("slippage-bps", "2.0"),
            "slippage-bps",
            100.0,
        )
        max_daily_loss = parse_positive_float(
            self.get_parameter("max-daily-loss", "0.02"),
            "max-daily-loss",
            0.02,
        )
        max_drawdown = parse_positive_float(
            self.get_parameter("max-drawdown", "0.05"),
            "max-drawdown",
            0.05,
        )

        self._signal_model = MovingAverageSignalModel(fast_period, slow_period, target_weight)
        self._portfolio_model = LongOnlyPortfolioModel(max_position, max_total_exposure)
        if target_weight > max_position or target_weight > max_total_exposure:
            raise ValueError("target-weight must fit within position and total exposure caps")
        self._risk_model = LatchedRiskModel(initial_cash, max_daily_loss, max_drawdown)
        self._configured_end_date = end

        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_time_zone(TimeZones.UTC)
        self.set_cash(initial_cash)
        self.set_brokerage_model(
            BrokerageName.QUANT_CONNECT_BROKERAGE,
            AccountType.CASH,
        )

        security = self.add_equity(
            ticker,
            Resolution.DAILY,
            fill_forward=False,
            data_normalization_mode=DataNormalizationMode.ADJUSTED,
        )
        security.set_leverage(1.0)
        security.set_fee_model(BpsMinimumFeeModel(fee_bps, minimum_fee))
        security.set_slippage_model(ConstantSlippageModel(slippage_bps / 10_000.0))
        self._security = security
        self._symbol = security.symbol
        self._execution_model = NextOpenExecutionModel(self._symbol)
        self._completed_closes: list[float] = []
        self._halt_liquidation_needed = False
        self.set_benchmark(self._symbol)
        if warmup_bars:
            self.set_warm_up(warmup_bars, Resolution.DAILY)

    def on_data(self, data: Slice) -> None:
        if not data.contains_key(self._symbol) or data[self._symbol] is None:
            return

        bar = data[self._symbol]
        if bar.is_fill_forward:
            self.debug("Ignoring fill-forward bar; no signal or order is permitted")
            return

        close = float(bar.close)
        self._completed_closes.append(close)
        if len(self._completed_closes) > self._signal_model.slow_period:
            self._completed_closes.pop(0)

        if self.is_warming_up:
            return

        equity = float(self.portfolio.total_portfolio_value)
        reasons = self._risk_model.close_session(self.time.date(), equity)
        if reasons:
            self._halt_liquidation_needed = True

        if self._risk_model.halted:
            self._submit_halt_liquidation()
            return

        signal_target = self._signal_model.target_for_completed_closes(
            tuple(self._completed_closes)
        )
        if signal_target is None:
            return
        target = self._portfolio_model.validate_target(signal_target)
        if not self._next_open_is_within_backtest():
            self.debug("Final completed bar has no eligible next-session open; target expires")
            return
        self._execution_model.submit_target(
            self,
            target,
            "completed-close signal; next-session MOO",
        )

    def _next_open_is_within_backtest(self) -> bool:
        next_open = self._security.exchange.hours.get_next_market_open(self.time, False)
        return next_open.date() <= self._configured_end_date

    def on_order_event(self, order_event) -> None:
        if order_event.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return
        equity = float(self.portfolio.total_portfolio_value)
        reasons = self._risk_model.observe(self.time.date(), equity)
        if reasons:
            self._halt_liquidation_needed = True
            self._execution_model.cancel_open_orders(self, "risk halt latched")

    def _submit_halt_liquidation(self) -> None:
        self._execution_model.cancel_open_orders(self, "risk halt latched")
        if not self._halt_liquidation_needed:
            return
        if not self.portfolio[self._symbol].invested:
            self._halt_liquidation_needed = False
            return
        self._execution_model.submit_target(
            self,
            0.0,
            "latched risk halt; conservative next-open liquidation",
        )

    def on_end_of_algorithm(self) -> None:
        self._execution_model.cancel_open_orders(
            self,
            "backtest ended; cancel pending MOO without fabricating a final fill",
        )
        if self._risk_model.halted:
            self.debug(f"Risk halt remained latched: {','.join(self._risk_model.halt_reasons)}")
