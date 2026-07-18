from __future__ import annotations

import pytest

from trading_bot_lab.domain import RiskReason, RiskStatus
from trading_bot_lab.risk import (
    OrderRequest,
    PortfolioSnapshot,
    RiskPolicy,
    evaluate_order,
    evaluate_portfolio_halt,
)


def base_portfolio(**overrides: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "equity": 100_000,
        "start_of_day_equity": 100_000,
        "peak_equity": 100_000,
        "daily_pnl": 0,
        "trading_enabled": True,
        "kill_switch_active": False,
        "cash": 100_000,
        "open_positions": 0,
        "halted": False,
    }
    values.update(overrides)
    return PortfolioSnapshot(**values)


def base_order(**overrides: object) -> OrderRequest:
    values: dict[str, object] = {
        "symbol": "SPY",
        "side": "buy",
        "quantity": 50,
        "reference_price": 100,
        "execution_price": 100,
        "notional": 5_000,
        "cash_required": 5_000,
        "available_cash": 100_000,
        "resulting_symbol_exposure": 5_000,
        "resulting_total_gross_exposure": 5_000,
        "resulting_quantity": 50,
        "data_age_seconds": 10,
        "is_live_order": False,
    }
    values.update(overrides)
    return OrderRequest(**values)


def test_small_simulated_order_is_approved() -> None:
    decision = evaluate_order(RiskPolicy(), base_order(), base_portfolio())

    assert decision.status is RiskStatus.APPROVED
    assert decision.reasons == ()
    assert decision.metrics["order_weight"] == 0.05


def test_live_order_is_always_rejected_and_cannot_be_enabled() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(is_live_order=True),
        base_portfolio(),
    )

    assert RiskReason.LIVE_TRADING_DISABLED in decision.reasons
    with pytest.raises(ValueError, match="not implemented"):
        RiskPolicy(allow_live_trading=True)


@pytest.mark.parametrize(
    ("portfolio", "reason"),
    [
        (base_portfolio(trading_enabled=False), RiskReason.TRADING_DISABLED),
        (base_portfolio(kill_switch_active=True), RiskReason.KILL_SWITCH),
        (base_portfolio(halted=True), RiskReason.HALTED),
    ],
)
def test_portfolio_gates_reject_new_orders(
    portfolio: PortfolioSnapshot,
    reason: RiskReason,
) -> None:
    decision = evaluate_order(RiskPolicy(), base_order(), portfolio)

    assert reason in decision.reasons


def test_stale_and_invalid_data_are_typed_rejections() -> None:
    stale = evaluate_order(
        RiskPolicy(max_data_age_seconds=300),
        base_order(data_age_seconds=301),
        base_portfolio(),
    )
    invalid = evaluate_order(
        RiskPolicy(),
        base_order(data_valid=False),
        base_portfolio(),
    )

    assert RiskReason.STALE_DATA in stale.reasons
    assert RiskReason.INVALID_DATA in invalid.reasons


@pytest.mark.parametrize("price_field", ["reference_price", "execution_price"])
def test_non_positive_execution_inputs_fail_closed(price_field: str) -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(**{price_field: 0}),
        base_portfolio(),
    )

    assert RiskReason.NON_POSITIVE_PRICE in decision.reasons


def test_non_positive_quantity_fails_closed() -> None:
    decision = evaluate_order(RiskPolicy(), base_order(quantity=0), base_portfolio())

    assert RiskReason.INVALID_QUANTITY in decision.reasons


def test_available_cash_check_includes_execution_cost_and_fee() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(cash_required=5_001, available_cash=5_000),
        base_portfolio(cash=5_000),
    )

    assert RiskReason.INSUFFICIENT_CASH in decision.reasons


@pytest.mark.parametrize(
    ("order", "reason"),
    [
        (base_order(notional=10_001), RiskReason.MAX_ORDER_NOTIONAL),
        (base_order(resulting_symbol_exposure=10_001), RiskReason.MAX_POSITION),
        (
            base_order(resulting_total_gross_exposure=30_001),
            RiskReason.MAX_TOTAL_EXPOSURE,
        ),
    ],
)
def test_notional_and_exposure_caps_reject(order: OrderRequest, reason: RiskReason) -> None:
    decision = evaluate_order(RiskPolicy(), order, base_portfolio())

    assert reason in decision.reasons


def test_maximum_open_position_count_rejects_new_symbol_risk() -> None:
    decision = evaluate_order(
        RiskPolicy(max_open_positions=1),
        base_order(open_positions=1, current_symbol_exposure=0),
        base_portfolio(open_positions=1),
    )

    assert RiskReason.MAX_OPEN_POSITIONS in decision.reasons


def test_duplicate_intent_is_rejected() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(intent_id="same", last_intent_id="same"),
        base_portfolio(),
    )

    assert RiskReason.DUPLICATE_INTENT in decision.reasons


def test_short_exposure_and_quantity_are_rejected() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(
            side="sell",
            resulting_symbol_exposure=-1,
            resulting_quantity=-0.01,
        ),
        base_portfolio(),
    )

    assert RiskReason.SHORTING in decision.reasons


def test_unsafe_shorting_and_leverage_policy_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="shorting"):
        RiskPolicy(allow_shorting=True)
    with pytest.raises(ValueError, match="leverage"):
        RiskPolicy(allow_leverage=True)


def test_disallowed_or_empty_symbol_is_rejected() -> None:
    disallowed = evaluate_order(RiskPolicy(), base_order(symbol="TSLA"), base_portfolio())
    empty = evaluate_order(RiskPolicy(), base_order(symbol=""), base_portfolio())

    assert RiskReason.INVALID_SYMBOL in disallowed.reasons
    assert RiskReason.INVALID_SYMBOL in empty.reasons


def test_daily_loss_and_drawdown_reject_at_threshold() -> None:
    daily = evaluate_portfolio_halt(
        RiskPolicy(max_daily_loss_pct=0.02),
        base_portfolio(equity=98_000, daily_pnl=-2_000),
    )
    drawdown = evaluate_portfolio_halt(
        RiskPolicy(max_drawdown_pct=0.05),
        base_portfolio(equity=95_000),
    )

    assert RiskReason.DAILY_LOSS in daily.reasons
    assert daily.metrics["daily_loss_pct"] == 0.02
    assert RiskReason.MAX_DRAWDOWN in drawdown.reasons
    assert drawdown.metrics["drawdown_pct"] == 0.05


def test_risk_reducing_order_can_reduce_an_existing_overweight_position() -> None:
    decision = evaluate_order(
        RiskPolicy(max_asset_weight=0.10, max_total_gross_exposure=0.10),
        base_order(
            side="sell",
            current_symbol_exposure=20_000,
            resulting_symbol_exposure=15_000,
            resulting_total_gross_exposure=15_000,
            resulting_quantity=150,
            reduces_risk=True,
        ),
        base_portfolio(open_positions=1),
    )

    assert decision.approved


def test_risk_reducing_order_still_cannot_bypass_staleness_or_halt() -> None:
    decision = evaluate_order(
        RiskPolicy(max_data_age_seconds=300),
        base_order(
            side="sell",
            data_age_seconds=301,
            current_symbol_exposure=10_000,
            resulting_symbol_exposure=5_000,
            resulting_total_gross_exposure=5_000,
            reduces_risk=True,
        ),
        base_portfolio(halted=True),
    )

    assert RiskReason.STALE_DATA in decision.reasons
    assert RiskReason.HALTED in decision.reasons


@pytest.mark.parametrize(
    ("order", "reason"),
    [
        (base_order(notional=float("nan")), RiskReason.ORDER_NOTIONAL_NON_FINITE),
        (
            base_order(resulting_total_gross_exposure=-1),
            RiskReason.TOTAL_GROSS_EXPOSURE_NEGATIVE,
        ),
        (base_order(data_age_seconds=-1), RiskReason.DATA_AGE_NEGATIVE),
    ],
)
def test_invalid_numeric_order_inputs_have_actionable_reasons(
    order: OrderRequest,
    reason: RiskReason,
) -> None:
    decision = evaluate_order(RiskPolicy(), order, base_portfolio())

    assert reason in decision.reasons


def test_non_finite_portfolio_equity_has_actionable_reason() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(),
        base_portfolio(equity=float("nan")),
    )

    assert RiskReason.PORTFOLIO_EQUITY_NON_FINITE in decision.reasons


def test_policy_rejects_invalid_limits_and_symbol_configuration() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        RiskPolicy(max_asset_weight=1.1)
    with pytest.raises(ValueError, match="duplicates"):
        RiskPolicy(allowed_symbols=("SPY", "spy"))
    with pytest.raises(ValueError, match="max_open_positions"):
        RiskPolicy(max_open_positions=0)
