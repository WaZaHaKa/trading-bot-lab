from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from tests.support import TargetSequenceStrategy, make_bars
from trading_bot_lab.backtesting import (
    BacktestConfig,
    MovingAverageStrategy,
    NoTradeStrategy,
    run_backtest,
)
from trading_bot_lab.domain import DataValidationError, MarketBar, RiskReason, Signal
from trading_bot_lab.risk import RiskPolicy


def permissive_config(**overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "max_daily_loss_pct": 1.0,
        "max_drawdown_pct": 1.0,
    }
    values.update(overrides)
    return BacktestConfig(**values)


def policy() -> RiskPolicy:
    return RiskPolicy(
        allowed_symbols=("SPY",),
        max_daily_loss_pct=1.0,
        max_drawdown_pct=1.0,
    )


def test_moving_average_uses_only_trailing_closes_and_warmup() -> None:
    strategy = MovingAverageStrategy(fast_window=2, slow_window=3, target_weight=0.1)

    assert strategy.target_for_closes([100, 101]) == 0
    assert strategy.target_for_closes([100, 101, 103]) == 0.1
    assert strategy.target_for_closes([103, 101, 100]) == 0


def test_no_trade_strategy_is_a_cash_control() -> None:
    bars = make_bars([100, 150, 50])
    result = run_backtest(bars, strategy=NoTradeStrategy(), policy=policy())

    assert result.trades == ()
    assert result.summary.ending_equity == result.summary.starting_cash
    assert result.summary.max_exposure == 0


def test_signal_from_close_executes_at_next_bar_open() -> None:
    bars = make_bars([100, 110, 120], opens=[99, 105, 115])
    strategy = TargetSequenceStrategy((0.1, 0.0, 0.0))
    result = run_backtest(
        bars,
        strategy=strategy,
        policy=policy(),
        config=permissive_config(),
    )

    first = result.trades[0]
    assert first.signal_date == "2024-01-01"
    assert first.date == "2024-01-02"
    assert first.reference_price == 105
    assert first.execution_price == 105


def test_pending_trade_requires_next_bar_open() -> None:
    first, second = make_bars([100, 101])
    second_without_open = MarketBar(
        timestamp=second.timestamp,
        symbol=second.symbol,
        close=second.close,
    )

    with pytest.raises(DataValidationError, match="requires open"):
        run_backtest(
            (first, second_without_open),
            strategy=TargetSequenceStrategy((0.1, 0.1)),
            policy=policy(),
            config=permissive_config(),
        )


def test_strategy_receives_only_each_historical_prefix() -> None:
    strategy = TargetSequenceStrategy((0.0, 0.0, 0.0, 0.0))
    bars = make_bars([100, 101, 102, 103])

    run_backtest(bars, strategy=strategy, policy=policy(), config=permissive_config())

    assert strategy.history_lengths == [1, 2, 3, 4]


def test_signal_with_future_timestamp_is_rejected() -> None:
    class FutureSignalStrategy:
        name = "future_signal"

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            latest = history[-1]
            return Signal(
                timestamp=latest.timestamp + timedelta(days=1),
                symbol=latest.symbol,
                target_weight=0,
                strategy_name=self.name,
            )

    with pytest.raises(DataValidationError, match="must match the latest"):
        run_backtest(make_bars([100]), strategy=FutureSignalStrategy(), policy=policy())


def test_history_is_immutable_to_strategy() -> None:
    class MutationProbeStrategy:
        name = "mutation_probe"
        mutation_blocked = False

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            try:
                history.append(history[-1])  # type: ignore[attr-defined]
            except AttributeError:
                self.mutation_blocked = True
            latest = history[-1]
            return Signal(latest.timestamp, latest.symbol, 0, self.name)

    strategy = MutationProbeStrategy()
    run_backtest(make_bars([100, 101]), strategy=strategy, policy=policy())

    assert strategy.mutation_blocked


def test_one_profitable_round_trip_has_known_realized_pnl() -> None:
    result = run_backtest(
        make_bars([100, 105, 110], opens=[100, 100, 110]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(),
    )

    assert len(result.trades) == 2
    assert result.summary.realized_pnl == pytest.approx(1_000)
    assert result.summary.unrealized_pnl == 0
    assert result.summary.ending_equity == pytest.approx(101_000)


def test_one_losing_round_trip_has_known_realized_pnl() -> None:
    result = run_backtest(
        make_bars([100, 95, 90], opens=[100, 100, 90]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(),
    )

    assert result.summary.realized_pnl == pytest.approx(-1_000)
    assert result.summary.ending_equity == pytest.approx(99_000)


def test_multiple_entries_update_weighted_average_cost() -> None:
    result = run_backtest(
        make_bars([100, 100, 110, 120], opens=[100, 100, 110, 120]),
        strategy=TargetSequenceStrategy((0.05, 0.10, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(),
    )

    assert [trade.side for trade in result.trades] == ["buy", "buy", "sell"]
    second_average = result.trades[1].average_cost_after
    assert 100 < second_average < 110
    assert result.summary.realized_pnl > 0


def test_fee_only_erosion_is_accounted_once() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(fee_bps=10),
    )

    assert result.summary.total_fees_paid == pytest.approx(20.0)
    assert result.summary.realized_pnl == 0
    assert result.summary.ending_equity == pytest.approx(99_980)


def test_minimum_fee_is_applied_per_fill() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(minimum_fee=5),
    )

    assert result.summary.total_fees_paid == 10
    assert all(fill.fee == 5 for fill in result.fills)


def test_slippage_only_erosion_is_reflected_in_pnl_and_metric() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(slippage_bps=10),
    )

    assert result.summary.estimated_slippage_cost == pytest.approx(20)
    assert result.summary.realized_pnl == pytest.approx(-20)
    assert result.summary.ending_equity == pytest.approx(99_980)


def test_open_position_reports_unrealized_pnl_without_forced_liquidation() -> None:
    result = run_backtest(
        make_bars([100, 110], opens=[100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1)),
        policy=policy(),
        config=permissive_config(),
    )

    assert len(result.trades) == 1
    assert result.summary.realized_pnl == 0
    assert result.summary.unrealized_pnl == pytest.approx(1_000)
    assert result.equity_curve[-1].quantity == 100


def test_cash_long_only_and_accounting_identity_hold_after_every_bar() -> None:
    result = run_backtest(
        make_bars([100, 105, 95, 110, 90]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(fee_bps=2, slippage_bps=3),
    )

    assert all(point.cash >= 0 and point.quantity >= 0 for point in result.equity_curve)
    for point in result.equity_curve:
        expected = (
            result.initial_equity
            + point.realized_pnl
            + point.unrealized_pnl
            - point.cumulative_fees
        )
        assert point.equity == pytest.approx(expected, abs=1e-5)


def test_daily_loss_halt_is_latched_even_after_price_recovery() -> None:
    result = run_backtest(
        make_bars([100, 100, 70, 120, 130]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=0.02,
            max_drawdown_pct=1.0,
        ),
        config=BacktestConfig(max_daily_loss_pct=0.02, max_drawdown_pct=1.0),
    )

    assert result.halt_state.active
    assert RiskReason.DAILY_LOSS in result.halt_state.reasons
    assert len(result.fills) == 1
    assert all(point.halt_state.active for point in result.equity_curve[2:])


def test_drawdown_halt_is_explicit_and_latched() -> None:
    result = run_backtest(
        make_bars([100, 110, 40, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=1.0,
            max_drawdown_pct=0.05,
        ),
        config=BacktestConfig(max_daily_loss_pct=1.0, max_drawdown_pct=0.05),
    )

    assert result.summary.risk_halt_triggered
    assert RiskReason.MAX_DRAWDOWN in result.halt_state.reasons
    assert result.halt_state.timestamp == result.equity_curve[2].timestamp


def test_warmup_period_suppresses_early_signals() -> None:
    strategy = TargetSequenceStrategy((0.1, 0.1, 0.1, 0.1))
    result = run_backtest(
        make_bars([100, 101, 102, 103]),
        strategy=strategy,
        policy=policy(),
        config=permissive_config(warmup_bars=2),
    )

    assert strategy.history_lengths == [3, 4]
    assert result.trades[0].signal_date == "2024-01-03"


def test_oversized_target_is_recorded_as_rejected_intent() -> None:
    result = run_backtest(
        make_bars([100, 100]),
        strategy=TargetSequenceStrategy((0.2, 0.2)),
        policy=policy(),
        config=permissive_config(),
    )

    assert result.trades == ()
    assert result.summary.rejected_order_count == 1
    assert RiskReason.MAX_ORDER_NOTIONAL in result.risk_rejections[0].reasons
    assert len(result.order_intents) == len(result.risk_decisions)


def test_benchmarks_use_first_open_and_cash_control() -> None:
    result = run_backtest(
        make_bars([100, 110], opens=[80, 100]),
        strategy=NoTradeStrategy(),
        policy=policy(),
    )

    assert result.benchmarks.buy_and_hold.ending_equity == pytest.approx(137_500)
    assert result.benchmarks.cash.ending_equity == 100_000


def test_repeated_runs_are_deterministic() -> None:
    bars = make_bars([100, 101, 102, 99])
    first = run_backtest(
        bars,
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(),
    )
    second = run_backtest(
        bars,
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(),
    )

    assert first.session_id == second.session_id
    assert first.summary == second.summary
    assert first.fills == second.fills


def test_direct_bars_still_reject_unsorted_or_duplicate_events() -> None:
    bars = make_bars([100, 101])

    with pytest.raises(DataValidationError, match="strictly ascending"):
        run_backtest(
            (bars[1], bars[0]),
            strategy=NoTradeStrategy(),
            policy=policy(),
        )


def test_inconsistent_exposure_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        BacktestConfig(max_position_pct=0.2, max_total_exposure_pct=0.1)
