"""Pure, fail-closed risk checks shared by every local simulation mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from trading_bot_lab.domain import DomainValidationError, RiskReason, RiskStatus, normalize_symbol

RISK_EPSILON = 1e-9


@dataclass(frozen=True)
class RiskPolicy:
    """Validated hard limits for backtests and historical paper replay.

    Live trading, leverage, and shorting are intentionally not configurable in
    the active MVP.  The legacy fields remain so unsafe configuration fails
    loudly instead of being silently ignored.
    """

    allow_live_trading: bool = False
    allow_shorting: bool = False
    allow_leverage: bool = False
    max_asset_weight: float = 0.10
    max_total_gross_exposure: float = 0.30
    max_order_notional_weight: float = 0.10
    max_daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.05
    max_data_age_seconds: int = 300
    max_open_positions: int = 1
    allowed_symbols: tuple[str, ...] = ("SPY", "QQQ", "BTCUSD", "ETHUSD")

    def __post_init__(self) -> None:
        for value, name in (
            (self.allow_live_trading, "allow_live_trading"),
            (self.allow_shorting, "allow_shorting"),
            (self.allow_leverage, "allow_leverage"),
        ):
            if type(value) is not bool:
                raise ValueError(f"{name} must be a bool")
        if self.allow_live_trading:
            raise ValueError("live trading is not implemented and must remain disabled")
        if self.allow_shorting:
            raise ValueError("shorting must remain disabled")
        if self.allow_leverage:
            raise ValueError("leverage must remain disabled")
        limits = {
            "max_asset_weight": self.max_asset_weight,
            "max_total_gross_exposure": self.max_total_gross_exposure,
            "max_order_notional_weight": self.max_order_notional_weight,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
        }
        for name, value in limits.items():
            if not _is_finite_number(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        if type(self.max_data_age_seconds) is not int or self.max_data_age_seconds < 0:
            raise ValueError("max_data_age_seconds must be a non-negative integer")
        if type(self.max_open_positions) is not int or self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be a positive integer")
        if not isinstance(self.allowed_symbols, tuple) or any(
            not isinstance(symbol, str) for symbol in self.allowed_symbols
        ):
            raise ValueError("allowed_symbols must be a tuple of strings")
        try:
            normalized = tuple(
                normalize_symbol(symbol, field_name="allowed symbol")
                for symbol in self.allowed_symbols
            )
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed_symbols must not contain duplicates")
        object.__setattr__(self, "allowed_symbols", normalized)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Portfolio state required by pre-trade and circuit-breaker checks."""

    equity: float
    start_of_day_equity: float
    peak_equity: float
    daily_pnl: float = 0.0
    trading_enabled: bool = True
    kill_switch_active: bool = False
    cash: float | None = None
    open_positions: int = 0
    halted: bool = False


@dataclass(frozen=True)
class OrderRequest:
    """Projected state for a proposed simulated order."""

    symbol: str
    side: Literal["buy", "sell"]
    notional: float
    resulting_symbol_exposure: float
    resulting_total_gross_exposure: float
    data_age_seconds: int
    is_live_order: bool = False
    quantity: float = 1.0
    reference_price: float = 1.0
    execution_price: float = 1.0
    estimated_fee: float = 0.0
    cash_required: float | None = None
    available_cash: float | None = None
    current_quantity: float = 0.0
    current_symbol_exposure: float = 0.0
    current_total_gross_exposure: float = 0.0
    resulting_quantity: float = 0.0
    money_precision: int = 8
    open_positions: int = 0
    intent_id: str | None = None
    last_intent_id: str | None = None
    data_valid: bool = True
    reduces_risk: bool = False


@dataclass(frozen=True)
class RiskDecision:
    """Typed approval/rejection with diagnostic metrics."""

    status: RiskStatus
    reasons: tuple[RiskReason, ...] = field(default_factory=tuple)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status is RiskStatus.APPROVED


def evaluate_order(
    policy: RiskPolicy,
    order: OrderRequest,
    portfolio: PortfolioSnapshot,
) -> RiskDecision:
    """Evaluate an order intent without side effects.

    Every invalid or unavailable input rejects the order.  Exposure/notional
    caps do not block an order proven from consistent projections to reduce
    risk, but cash, no-short, stale-data, halt, and validity checks remain.
    """

    reasons: list[RiskReason] = []
    metrics: dict[str, float] = {}
    _validate_portfolio(portfolio, reasons)

    symbol_valid = isinstance(order.symbol, str) and bool(order.symbol.strip())
    if not symbol_valid or order.symbol.strip().upper() not in policy.allowed_symbols:
        reasons.append(RiskReason.INVALID_SYMBOL)
    side_valid = isinstance(order.side, str) and order.side in {"buy", "sell"}
    if not side_valid:
        reasons.append(RiskReason.INVALID_ORDER)

    if type(order.data_valid) is not bool:
        reasons.append(RiskReason.INVALID_ORDER)
    elif not order.data_valid:
        reasons.append(RiskReason.INVALID_DATA)
    if type(order.is_live_order) is not bool:
        reasons.append(RiskReason.INVALID_ORDER)
    elif order.is_live_order:
        reasons.append(RiskReason.LIVE_TRADING_DISABLED)
    if type(order.reduces_risk) is not bool:
        reasons.append(RiskReason.INVALID_ORDER)

    quantity_valid = _is_finite_number(order.quantity) and order.quantity > 0
    reference_valid = _is_finite_number(order.reference_price) and order.reference_price > 0
    execution_valid = _is_finite_number(order.execution_price) and order.execution_price > 0
    fee_valid = _is_finite_number(order.estimated_fee) and order.estimated_fee >= 0
    current_exposure_valid = (
        _is_finite_number(order.current_symbol_exposure) and order.current_symbol_exposure >= 0
    )
    current_total_exposure_valid = (
        _is_finite_number(order.current_total_gross_exposure)
        and order.current_total_gross_exposure >= 0
    )
    current_quantity_valid = (
        _is_finite_number(order.current_quantity) and order.current_quantity >= 0
    )
    resulting_exposure_valid = _is_finite_number(order.resulting_symbol_exposure)
    total_exposure_valid = _is_finite_number(order.resulting_total_gross_exposure)
    resulting_quantity_valid = _is_finite_number(order.resulting_quantity)
    money_precision_valid = type(order.money_precision) is int and 8 <= order.money_precision <= 12

    if not quantity_valid:
        reasons.append(RiskReason.INVALID_QUANTITY)
    if not reference_valid or not execution_valid:
        reasons.append(RiskReason.NON_POSITIVE_PRICE)
    if not _is_finite_number(order.notional):
        reasons.append(RiskReason.ORDER_NOTIONAL_NON_FINITE)
    elif order.notional <= 0:
        reasons.append(RiskReason.ORDER_NOTIONAL_NON_POSITIVE)
    if (
        not fee_valid
        or not current_exposure_valid
        or not current_total_exposure_valid
        or not current_quantity_valid
        or not resulting_exposure_valid
    ):
        reasons.append(RiskReason.INVALID_ORDER)
    if not total_exposure_valid:
        reasons.append(RiskReason.INVALID_ORDER)
    elif order.resulting_total_gross_exposure < 0:
        reasons.append(RiskReason.TOTAL_GROSS_EXPOSURE_NEGATIVE)
    if not resulting_quantity_valid:
        reasons.append(RiskReason.INVALID_QUANTITY)
    elif order.resulting_quantity < -RISK_EPSILON:
        reasons.append(RiskReason.SHORTING)
    if resulting_exposure_valid and order.resulting_symbol_exposure < -RISK_EPSILON:
        reasons.append(RiskReason.SHORTING)
    if not money_precision_valid:
        reasons.append(RiskReason.INVALID_ORDER)

    if type(order.data_age_seconds) is not int:
        reasons.append(RiskReason.INVALID_DATA)
    elif order.data_age_seconds < 0:
        reasons.append(RiskReason.DATA_AGE_NEGATIVE)
    elif order.data_age_seconds > policy.max_data_age_seconds:
        reasons.append(RiskReason.STALE_DATA)
    if (
        type(order.open_positions) is not int
        or order.open_positions < 0
        or (
            type(portfolio.open_positions) is int
            and order.open_positions != portfolio.open_positions
        )
    ):
        reasons.append(RiskReason.INVALID_PORTFOLIO)

    if order.intent_id is not None and order.intent_id == order.last_intent_id:
        reasons.append(RiskReason.DUPLICATE_INTENT)

    execution_notional: float | None = None
    slippage_cost: float | None = None
    if quantity_valid and reference_valid and execution_valid:
        derived_reference_notional = order.quantity * order.reference_price
        derived_execution_notional = order.quantity * order.execution_price
        derived_slippage_cost = order.quantity * abs(order.execution_price - order.reference_price)
        if all(
            _is_finite_number(value)
            for value in (
                derived_reference_notional,
                derived_execution_notional,
                derived_slippage_cost,
            )
        ):
            execution_notional = derived_execution_notional
            slippage_cost = derived_slippage_cost
        else:
            reasons.append(RiskReason.ORDER_NOTIONAL_NON_FINITE)
        if _is_finite_number(order.notional) and not _matches(
            order.notional,
            derived_execution_notional,
        ):
            reasons.append(RiskReason.INVALID_ORDER)
        if order.side == "buy" and order.execution_price < order.reference_price:
            reasons.append(RiskReason.INVALID_ORDER)
        if order.side == "sell" and order.execution_price > order.reference_price:
            reasons.append(RiskReason.INVALID_ORDER)

    if resulting_quantity_valid and reference_valid and resulting_exposure_valid:
        derived_exposure = order.resulting_quantity * order.reference_price
        if not _matches(order.resulting_symbol_exposure, derived_exposure):
            reasons.append(RiskReason.INVALID_ORDER)
    if current_quantity_valid and reference_valid and current_exposure_valid:
        derived_current_exposure = order.current_quantity * order.reference_price
        if not _matches(order.current_symbol_exposure, derived_current_exposure):
            reasons.append(RiskReason.INVALID_ORDER)
    if current_quantity_valid and resulting_quantity_valid and quantity_valid and side_valid:
        quantity_delta = order.quantity if order.side == "buy" else -order.quantity
        if not _matches(order.resulting_quantity, order.current_quantity + quantity_delta):
            reasons.append(RiskReason.INVALID_ORDER)
    if (
        current_total_exposure_valid
        and current_exposure_valid
        and resulting_exposure_valid
        and total_exposure_valid
    ):
        derived_total_exposure = (
            order.current_total_gross_exposure
            - order.current_symbol_exposure
            + abs(order.resulting_symbol_exposure)
        )
        if not _matches(order.resulting_total_gross_exposure, derived_total_exposure):
            reasons.append(RiskReason.INVALID_ORDER)
    if (
        current_total_exposure_valid
        and current_exposure_valid
        and order.current_total_gross_exposure + RISK_EPSILON < order.current_symbol_exposure
    ):
        reasons.append(RiskReason.INVALID_ORDER)
    if (
        resulting_exposure_valid
        and total_exposure_valid
        and order.resulting_total_gross_exposure + RISK_EPSILON
        < abs(order.resulting_symbol_exposure)
    ):
        reasons.append(RiskReason.INVALID_ORDER)

    authoritative_cash = portfolio.cash
    cash_valid = _is_finite_number(authoritative_cash) and authoritative_cash >= 0
    if order.available_cash is not None and (
        not _is_finite_number(order.available_cash)
        or order.available_cash < 0
        or (cash_valid and not _matches(order.available_cash, authoritative_cash))
    ):
        reasons.append(RiskReason.INVALID_PORTFOLIO)

    if (
        cash_valid
        and money_precision_valid
        and current_total_exposure_valid
        and _is_finite_number(portfolio.equity)
    ):
        rounded_current_exposure = _round_if_finite(
            order.current_total_gross_exposure,
            order.money_precision,
        )
        expected_equity = (
            None
            if rounded_current_exposure is None
            else _round_if_finite(
                authoritative_cash + rounded_current_exposure,
                order.money_precision,
            )
        )
        if expected_equity is None or not _matches(portfolio.equity, expected_equity):
            reasons.append(RiskReason.INVALID_PORTFOLIO)

    projected_cash: float | None = None
    projected_equity: float | None = None
    if (
        side_valid
        and execution_notional is not None
        and slippage_cost is not None
        and fee_valid
        and cash_valid
        and money_precision_valid
        and _is_finite_number(portfolio.equity)
    ):
        cash_execution_notional = _round_if_finite(
            execution_notional,
            order.money_precision,
        )
        if cash_execution_notional is None:
            reasons.append(RiskReason.INVALID_ORDER)
            raw_projected_cash = float("nan")
        elif order.side == "buy":
            if cash_execution_notional <= 0:
                reasons.append(RiskReason.INVALID_ORDER)
            derived_cash_required = cash_execution_notional + order.estimated_fee
            if order.cash_required is not None and (
                not _is_finite_number(order.cash_required)
                or not _matches(order.cash_required, derived_cash_required)
            ):
                reasons.append(RiskReason.INVALID_ORDER)
            raw_projected_cash = authoritative_cash - derived_cash_required
        else:
            if order.cash_required is not None:
                reasons.append(RiskReason.INVALID_ORDER)
            raw_projected_cash = authoritative_cash + cash_execution_notional - order.estimated_fee
        projected_cash = _round_if_finite(raw_projected_cash, order.money_precision)
        rounded_resulting_exposure = _round_if_finite(
            order.resulting_total_gross_exposure,
            order.money_precision,
        )
        projected_equity = (
            None
            if projected_cash is None or rounded_resulting_exposure is None
            else _round_if_finite(
                projected_cash + rounded_resulting_exposure,
                order.money_precision,
            )
        )
        if projected_cash is None or projected_equity is None:
            reasons.append(RiskReason.INVALID_ORDER)
            projected_cash = None
            projected_equity = None
        else:
            metrics["projected_cash"] = projected_cash
            metrics["projected_equity"] = projected_equity
            if projected_cash < 0:
                reasons.append(RiskReason.INSUFFICIENT_CASH)
            if projected_equity <= 0:
                reasons.append(RiskReason.PROJECTED_EQUITY_NON_POSITIVE)

    actually_reduces_risk = bool(
        order.side == "sell"
        and current_exposure_valid
        and current_total_exposure_valid
        and resulting_exposure_valid
        and total_exposure_valid
        and resulting_quantity_valid
        and order.resulting_quantity >= 0
        and order.resulting_symbol_exposure < order.current_symbol_exposure - RISK_EPSILON
        and order.resulting_total_gross_exposure < order.current_total_gross_exposure - RISK_EPSILON
    )
    if order.reduces_risk is True and not actually_reduces_risk:
        reasons.append(RiskReason.INVALID_ORDER)

    can_compute_weights = (
        _is_finite_number(portfolio.equity)
        and portfolio.equity > 0
        and execution_notional is not None
        and resulting_exposure_valid
        and total_exposure_valid
        and projected_equity is not None
        and projected_equity > 0
    )
    if can_compute_weights:
        projected_asset_value = round(
            abs(order.resulting_symbol_exposure),
            order.money_precision,
        )
        projected_total_value = round(
            order.resulting_total_gross_exposure,
            order.money_precision,
        )
        order_weight = execution_notional / portfolio.equity
        asset_weight = projected_asset_value / projected_equity
        total_gross_weight = projected_total_value / projected_equity
        if not all(
            _is_finite_number(value) for value in (order_weight, asset_weight, total_gross_weight)
        ):
            reasons.append(RiskReason.INVALID_ORDER)
        else:
            metrics.update(
                order_weight=order_weight,
                asset_weight=asset_weight,
                total_gross_weight=total_gross_weight,
                reduces_risk=1.0 if actually_reduces_risk else 0.0,
            )
            if not actually_reduces_risk:
                if order_weight - policy.max_order_notional_weight > RISK_EPSILON:
                    reasons.append(RiskReason.MAX_ORDER_NOTIONAL)
                if asset_weight - policy.max_asset_weight > RISK_EPSILON:
                    reasons.append(RiskReason.MAX_POSITION)
                if total_gross_weight - policy.max_total_gross_exposure > RISK_EPSILON:
                    reasons.append(RiskReason.MAX_TOTAL_EXPOSURE)
                if total_gross_weight - 1.0 > RISK_EPSILON:
                    reasons.append(RiskReason.LEVERAGE)

    opening_new_position = bool(
        current_exposure_valid
        and resulting_exposure_valid
        and order.current_symbol_exposure <= RISK_EPSILON
        and order.resulting_symbol_exposure > RISK_EPSILON
    )
    if opening_new_position and portfolio.open_positions >= policy.max_open_positions:
        reasons.append(RiskReason.MAX_OPEN_POSITIONS)

    _evaluate_loss_limits(policy, portfolio, reasons, metrics)
    return _decision(reasons, metrics)


def evaluate_portfolio_halt(
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
) -> RiskDecision:
    """Evaluate portfolio-level circuit breakers without requiring an order."""

    reasons: list[RiskReason] = []
    metrics: dict[str, float] = {}
    _validate_portfolio(portfolio, reasons)
    _evaluate_loss_limits(policy, portfolio, reasons, metrics)
    return _decision(reasons, metrics)


def _validate_portfolio(
    portfolio: PortfolioSnapshot,
    reasons: list[RiskReason],
) -> None:
    if not _is_finite_number(portfolio.equity):
        reasons.append(RiskReason.PORTFOLIO_EQUITY_NON_FINITE)
    elif portfolio.equity <= 0:
        reasons.append(RiskReason.PORTFOLIO_EQUITY_NON_POSITIVE)
    if not _is_finite_number(portfolio.start_of_day_equity) or portfolio.start_of_day_equity <= 0:
        reasons.append(RiskReason.START_OF_DAY_EQUITY_INVALID)
    if not _is_finite_number(portfolio.peak_equity) or portfolio.peak_equity <= 0:
        reasons.append(RiskReason.PEAK_EQUITY_INVALID)
    if not _is_finite_number(portfolio.daily_pnl):
        reasons.append(RiskReason.DAILY_PNL_NON_FINITE)
    elif (
        _is_finite_number(portfolio.equity)
        and _is_finite_number(portfolio.start_of_day_equity)
        and not _matches(
            portfolio.daily_pnl,
            portfolio.equity - portfolio.start_of_day_equity,
        )
    ):
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    if portfolio.cash is None or not _is_finite_number(portfolio.cash) or portfolio.cash < 0:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    if type(portfolio.open_positions) is not int or portfolio.open_positions < 0:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    if type(portfolio.trading_enabled) is not bool:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    elif not portfolio.trading_enabled:
        reasons.append(RiskReason.TRADING_DISABLED)
    if type(portfolio.kill_switch_active) is not bool:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    elif portfolio.kill_switch_active:
        reasons.append(RiskReason.KILL_SWITCH)
    if type(portfolio.halted) is not bool:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    elif portfolio.halted:
        reasons.append(RiskReason.HALTED)


def _evaluate_loss_limits(
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
    reasons: list[RiskReason],
    metrics: dict[str, float],
) -> None:
    if (
        _is_finite_number(portfolio.start_of_day_equity)
        and portfolio.start_of_day_equity > 0
        and _is_finite_number(portfolio.equity)
    ):
        derived_daily_pnl = portfolio.equity - portfolio.start_of_day_equity
        daily_loss_pct = max(0.0, -derived_daily_pnl) / portfolio.start_of_day_equity
        metrics["daily_loss_pct"] = daily_loss_pct
        if daily_loss_pct >= policy.max_daily_loss_pct:
            reasons.append(RiskReason.DAILY_LOSS)
    if (
        _is_finite_number(portfolio.peak_equity)
        and portfolio.peak_equity > 0
        and _is_finite_number(portfolio.equity)
    ):
        drawdown_pct = max(0.0, portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
        metrics["drawdown_pct"] = drawdown_pct
        if drawdown_pct >= policy.max_drawdown_pct:
            reasons.append(RiskReason.MAX_DRAWDOWN)


def _decision(reasons: list[RiskReason], metrics: dict[str, float]) -> RiskDecision:
    unique_reasons = tuple(dict.fromkeys(reasons))
    if unique_reasons:
        return RiskDecision(RiskStatus.REJECTED, unique_reasons, metrics)
    return RiskDecision(RiskStatus.APPROVED, (), metrics)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _matches(left: float, right: float) -> bool:
    if not _is_finite_number(left) or not _is_finite_number(right):
        return False
    tolerance = max(RISK_EPSILON, abs(right) * 1e-12)
    return abs(left - right) <= tolerance


def _round_if_finite(value: float, precision: int) -> float | None:
    if not _is_finite_number(value):
        return None
    rounded = round(value, precision)
    return rounded if _is_finite_number(rounded) else None


__all__ = [
    "OrderRequest",
    "PortfolioSnapshot",
    "RiskDecision",
    "RiskPolicy",
    "RiskReason",
    "RiskStatus",
    "evaluate_order",
    "evaluate_portfolio_halt",
]
