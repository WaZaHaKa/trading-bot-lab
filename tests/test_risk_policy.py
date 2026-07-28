from __future__ import annotations

from math import isfinite

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
        base_order(
            estimated_fee=1,
            cash_required=5_001,
            available_cash=5_000,
        ),
        base_portfolio(
            cash=5_000,
            equity=5_000,
            start_of_day_equity=5_000,
            peak_equity=5_000,
        ),
    )

    assert RiskReason.INSUFFICIENT_CASH in decision.reasons


@pytest.mark.parametrize(
    ("order", "portfolio", "reason"),
    [
        (
            base_order(
                quantity=100.01,
                notional=10_001,
                cash_required=10_001,
                resulting_symbol_exposure=10_001,
                resulting_total_gross_exposure=10_001,
                resulting_quantity=100.01,
            ),
            base_portfolio(),
            RiskReason.MAX_ORDER_NOTIONAL,
        ),
        (
            base_order(
                quantity=20,
                notional=2_000,
                cash_required=2_000,
                current_quantity=90,
                current_symbol_exposure=9_000,
                current_total_gross_exposure=9_000,
                resulting_symbol_exposure=11_000,
                resulting_total_gross_exposure=11_000,
                resulting_quantity=110,
                open_positions=1,
                available_cash=91_000,
            ),
            base_portfolio(cash=91_000, open_positions=1),
            RiskReason.MAX_POSITION,
        ),
        (
            base_order(
                current_quantity=260,
                current_symbol_exposure=26_000,
                current_total_gross_exposure=26_000,
                resulting_symbol_exposure=31_000,
                resulting_total_gross_exposure=31_000,
                resulting_quantity=310,
                open_positions=1,
                available_cash=74_000,
            ),
            base_portfolio(cash=74_000, open_positions=1),
            RiskReason.MAX_TOTAL_EXPOSURE,
        ),
    ],
)
def test_notional_and_exposure_caps_reject(
    order: OrderRequest,
    portfolio: PortfolioSnapshot,
    reason: RiskReason,
) -> None:
    decision = evaluate_order(RiskPolicy(), order, portfolio)

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


def test_daily_pnl_is_derived_and_inconsistent_snapshot_fails_closed() -> None:
    decision = evaluate_portfolio_halt(
        RiskPolicy(max_daily_loss_pct=0.02, max_drawdown_pct=0.05),
        base_portfolio(equity=98_000, daily_pnl=0),
    )

    assert RiskReason.INVALID_PORTFOLIO in decision.reasons
    assert RiskReason.DAILY_LOSS in decision.reasons
    assert decision.metrics["daily_loss_pct"] == 0.02


def test_risk_reducing_order_can_reduce_an_existing_overweight_position() -> None:
    decision = evaluate_order(
        RiskPolicy(max_asset_weight=0.10, max_total_gross_exposure=0.10),
        base_order(
            side="sell",
            cash_required=None,
            current_quantity=200,
            current_symbol_exposure=20_000,
            current_total_gross_exposure=20_000,
            resulting_symbol_exposure=15_000,
            resulting_total_gross_exposure=15_000,
            resulting_quantity=150,
            open_positions=1,
            available_cash=80_000,
            reduces_risk=True,
        ),
        base_portfolio(cash=80_000, open_positions=1),
    )

    assert decision.approved


def test_multi_position_total_exposure_is_used_to_derive_risk_reduction() -> None:
    decision = evaluate_order(
        RiskPolicy(
            max_asset_weight=0.10,
            max_total_gross_exposure=0.10,
            max_open_positions=2,
        ),
        base_order(
            side="sell",
            cash_required=None,
            available_cash=70_000,
            current_quantity=200,
            current_symbol_exposure=20_000,
            current_total_gross_exposure=30_000,
            resulting_symbol_exposure=15_000,
            resulting_total_gross_exposure=25_000,
            resulting_quantity=150,
            open_positions=2,
            reduces_risk=True,
        ),
        base_portfolio(cash=70_000, open_positions=2),
    )

    assert decision.approved
    assert decision.metrics["reduces_risk"] == 1


def test_risk_reducing_order_still_cannot_bypass_staleness_or_halt() -> None:
    decision = evaluate_order(
        RiskPolicy(max_data_age_seconds=300),
        base_order(
            side="sell",
            cash_required=None,
            data_age_seconds=301,
            current_quantity=100,
            current_symbol_exposure=10_000,
            current_total_gross_exposure=10_000,
            resulting_symbol_exposure=5_000,
            resulting_total_gross_exposure=5_000,
            resulting_quantity=50,
            open_positions=1,
            available_cash=90_000,
            reduces_risk=True,
        ),
        base_portfolio(cash=90_000, halted=True, open_positions=1),
    )

    assert RiskReason.STALE_DATA in decision.reasons
    assert RiskReason.HALTED in decision.reasons


def test_caller_cannot_label_an_oversized_buy_as_risk_reducing() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(
            quantity=200,
            notional=20_000,
            cash_required=20_000,
            resulting_symbol_exposure=20_000,
            resulting_total_gross_exposure=20_000,
            resulting_quantity=200,
            reduces_risk=True,
        ),
        base_portfolio(),
    )

    assert RiskReason.INVALID_ORDER in decision.reasons
    assert RiskReason.MAX_ORDER_NOTIONAL in decision.reasons
    assert RiskReason.MAX_POSITION in decision.reasons


def test_inconsistent_projected_position_fails_closed() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(resulting_quantity=1, resulting_symbol_exposure=100),
        base_portfolio(),
    )

    assert decision.reasons == (RiskReason.INVALID_ORDER,)


def test_missing_or_inconsistent_authoritative_cash_fails_closed() -> None:
    missing = evaluate_order(RiskPolicy(), base_order(), base_portfolio(cash=None))
    inconsistent = evaluate_order(
        RiskPolicy(),
        base_order(available_cash=99_000),
        base_portfolio(cash=100_000),
    )

    assert RiskReason.INVALID_PORTFOLIO in missing.reasons
    assert RiskReason.INVALID_PORTFOLIO in inconsistent.reasons


def test_sell_fee_that_would_make_cash_negative_is_rejected() -> None:
    decision = evaluate_order(
        RiskPolicy(
            max_asset_weight=1.0,
            max_total_gross_exposure=1.0,
            max_daily_loss_pct=1.0,
            max_drawdown_pct=1.0,
        ),
        base_order(
            side="sell",
            quantity=50,
            notional=5_000,
            estimated_fee=20_000,
            cash_required=None,
            available_cash=0,
            current_quantity=100,
            current_symbol_exposure=10_000,
            current_total_gross_exposure=10_000,
            resulting_symbol_exposure=5_000,
            resulting_total_gross_exposure=5_000,
            resulting_quantity=50,
            open_positions=1,
            reduces_risk=True,
        ),
        base_portfolio(
            cash=0,
            equity=10_000,
            start_of_day_equity=10_000,
            peak_equity=10_000,
            open_positions=1,
        ),
    )

    assert RiskReason.INSUFFICIENT_CASH in decision.reasons
    assert RiskReason.PROJECTED_EQUITY_NON_POSITIVE in decision.reasons


def test_non_finite_projected_quantity_is_typed_rejection() -> None:
    decision = evaluate_order(
        RiskPolicy(),
        base_order(
            resulting_quantity=float("nan"),
            resulting_symbol_exposure=float("nan"),
        ),
        base_portfolio(),
    )

    assert RiskReason.INVALID_QUANTITY in decision.reasons
    assert RiskReason.INVALID_ORDER in decision.reasons


def test_derived_arithmetic_overflow_rejects_without_infinite_metrics() -> None:
    decision = evaluate_order(
        RiskPolicy(
            max_asset_weight=1.0,
            max_total_gross_exposure=1.0,
            max_order_notional_weight=1.0,
            max_daily_loss_pct=1.0,
            max_drawdown_pct=1.0,
        ),
        base_order(
            side="sell",
            quantity=1e308,
            reference_price=10,
            execution_price=10,
            notional=1e308,
            cash_required=None,
            available_cash=0,
            current_quantity=1e308,
            current_symbol_exposure=1e308,
            current_total_gross_exposure=1e308,
            resulting_symbol_exposure=0,
            resulting_total_gross_exposure=0,
            resulting_quantity=0,
            open_positions=1,
            reduces_risk=True,
        ),
        base_portfolio(
            cash=0,
            equity=1e308,
            start_of_day_equity=1e308,
            peak_equity=1e308,
            open_positions=1,
        ),
    )

    assert RiskReason.ORDER_NOTIONAL_NON_FINITE in decision.reasons
    assert not decision.approved
    assert all(isfinite(value) for value in decision.metrics.values())


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
    with pytest.raises(ValueError, match="must be a bool"):
        RiskPolicy(allow_live_trading=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        RiskPolicy(max_asset_weight=float("nan"))
    with pytest.raises(ValueError, match="non-negative integer"):
        RiskPolicy(max_data_age_seconds=True)
