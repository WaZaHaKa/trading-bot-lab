"""Pure, fail-closed risk checks shared by every local simulation mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from trading_bot_lab.domain import RiskReason, RiskStatus

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
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        if self.max_data_age_seconds < 0:
            raise ValueError("max_data_age_seconds must be non-negative")
        if self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        normalized = tuple(symbol.strip().upper() for symbol in self.allowed_symbols)
        if any(not symbol for symbol in normalized):
            raise ValueError("allowed_symbols must not contain empty symbols")
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
    current_symbol_exposure: float = 0.0
    resulting_quantity: float = 0.0
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
    caps do not block an order explicitly marked as risk-reducing, but cash,
    no-short, stale-data, halt, and validity checks always remain mandatory.
    """

    reasons: list[RiskReason] = []
    metrics: dict[str, float] = {}
    _validate_portfolio(portfolio, reasons)

    if not order.symbol.strip() or order.symbol.strip().upper() not in policy.allowed_symbols:
        reasons.append(RiskReason.INVALID_SYMBOL)
    if order.side not in {"buy", "sell"}:
        reasons.append(RiskReason.INVALID_ORDER)
    if not order.data_valid:
        reasons.append(RiskReason.INVALID_DATA)
    if not isfinite(order.quantity) or order.quantity <= 0:
        reasons.append(RiskReason.INVALID_QUANTITY)
    if not isfinite(order.reference_price) or order.reference_price <= 0:
        reasons.append(RiskReason.NON_POSITIVE_PRICE)
    if not isfinite(order.execution_price) or order.execution_price <= 0:
        reasons.append(RiskReason.NON_POSITIVE_PRICE)
    if not isfinite(order.notional):
        reasons.append(RiskReason.ORDER_NOTIONAL_NON_FINITE)
    elif order.notional <= 0:
        reasons.append(RiskReason.ORDER_NOTIONAL_NON_POSITIVE)
    if not isfinite(order.estimated_fee) or order.estimated_fee < 0:
        reasons.append(RiskReason.INVALID_ORDER)
    if not isfinite(order.resulting_symbol_exposure):
        reasons.append(RiskReason.INVALID_ORDER)
    if not isfinite(order.resulting_total_gross_exposure):
        reasons.append(RiskReason.INVALID_ORDER)
    elif order.resulting_total_gross_exposure < 0:
        reasons.append(RiskReason.TOTAL_GROSS_EXPOSURE_NEGATIVE)
    if not isfinite(float(order.data_age_seconds)):
        reasons.append(RiskReason.INVALID_DATA)
    elif order.data_age_seconds < 0:
        reasons.append(RiskReason.DATA_AGE_NEGATIVE)
    elif order.data_age_seconds > policy.max_data_age_seconds:
        reasons.append(RiskReason.STALE_DATA)

    if order.is_live_order:
        reasons.append(RiskReason.LIVE_TRADING_DISABLED)
    if order.resulting_symbol_exposure < -RISK_EPSILON or order.resulting_quantity < -RISK_EPSILON:
        reasons.append(RiskReason.SHORTING)
    if order.intent_id is not None and order.intent_id == order.last_intent_id:
        reasons.append(RiskReason.DUPLICATE_INTENT)

    if order.side == "buy" and order.available_cash is not None:
        if not isfinite(order.available_cash) or order.available_cash < 0:
            reasons.append(RiskReason.INVALID_PORTFOLIO)
        else:
            cash_required = (
                order.cash_required
                if order.cash_required is not None
                else order.notional + order.estimated_fee
            )
            if not isfinite(cash_required) or cash_required <= 0:
                reasons.append(RiskReason.INVALID_ORDER)
            elif cash_required - order.available_cash > RISK_EPSILON:
                reasons.append(RiskReason.INSUFFICIENT_CASH)

    can_compute_weights = (
        isfinite(portfolio.equity)
        and portfolio.equity > 0
        and isfinite(order.notional)
        and isfinite(order.resulting_symbol_exposure)
        and isfinite(order.resulting_total_gross_exposure)
    )
    if can_compute_weights:
        order_weight = order.notional / portfolio.equity
        asset_weight = abs(order.resulting_symbol_exposure) / portfolio.equity
        total_gross_weight = order.resulting_total_gross_exposure / portfolio.equity
        metrics.update(
            order_weight=order_weight,
            asset_weight=asset_weight,
            total_gross_weight=total_gross_weight,
        )
        if not order.reduces_risk:
            if order_weight - policy.max_order_notional_weight > RISK_EPSILON:
                reasons.append(RiskReason.MAX_ORDER_NOTIONAL)
            if asset_weight - policy.max_asset_weight > RISK_EPSILON:
                reasons.append(RiskReason.MAX_POSITION)
            if total_gross_weight - policy.max_total_gross_exposure > RISK_EPSILON:
                reasons.append(RiskReason.MAX_TOTAL_EXPOSURE)
            if total_gross_weight - 1.0 > RISK_EPSILON:
                reasons.append(RiskReason.LEVERAGE)

    opening_new_position = (
        order.current_symbol_exposure <= RISK_EPSILON
        and order.resulting_symbol_exposure > RISK_EPSILON
    )
    if opening_new_position and order.open_positions >= policy.max_open_positions:
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
    if not isfinite(portfolio.equity):
        reasons.append(RiskReason.PORTFOLIO_EQUITY_NON_FINITE)
    elif portfolio.equity <= 0:
        reasons.append(RiskReason.PORTFOLIO_EQUITY_NON_POSITIVE)
    if not isfinite(portfolio.start_of_day_equity) or portfolio.start_of_day_equity <= 0:
        reasons.append(RiskReason.START_OF_DAY_EQUITY_INVALID)
    if not isfinite(portfolio.peak_equity) or portfolio.peak_equity <= 0:
        reasons.append(RiskReason.PEAK_EQUITY_INVALID)
    if not isfinite(portfolio.daily_pnl):
        reasons.append(RiskReason.DAILY_PNL_NON_FINITE)
    if portfolio.open_positions < 0:
        reasons.append(RiskReason.INVALID_PORTFOLIO)
    if not portfolio.trading_enabled:
        reasons.append(RiskReason.TRADING_DISABLED)
    if portfolio.kill_switch_active:
        reasons.append(RiskReason.KILL_SWITCH)
    if portfolio.halted:
        reasons.append(RiskReason.HALTED)


def _evaluate_loss_limits(
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
    reasons: list[RiskReason],
    metrics: dict[str, float],
) -> None:
    if (
        isfinite(portfolio.start_of_day_equity)
        and portfolio.start_of_day_equity > 0
        and isfinite(portfolio.daily_pnl)
    ):
        daily_loss_pct = max(0.0, -portfolio.daily_pnl) / portfolio.start_of_day_equity
        metrics["daily_loss_pct"] = daily_loss_pct
        if daily_loss_pct >= policy.max_daily_loss_pct:
            reasons.append(RiskReason.DAILY_LOSS)
    if isfinite(portfolio.peak_equity) and portfolio.peak_equity > 0 and isfinite(portfolio.equity):
        drawdown_pct = max(0.0, portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
        metrics["drawdown_pct"] = drawdown_pct
        if drawdown_pct >= policy.max_drawdown_pct:
            reasons.append(RiskReason.MAX_DRAWDOWN)


def _decision(reasons: list[RiskReason], metrics: dict[str, float]) -> RiskDecision:
    unique_reasons = tuple(dict.fromkeys(reasons))
    if unique_reasons:
        return RiskDecision(RiskStatus.REJECTED, unique_reasons, metrics)
    return RiskDecision(RiskStatus.APPROVED, (), metrics)


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
