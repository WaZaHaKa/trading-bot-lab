from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from tests.support import TargetSequenceStrategy, make_bars
from trading_bot_lab.backtesting import (
    BacktestConfig,
    MovingAverageStrategy,
    NoTradeStrategy,
    SimulationEngine,
    run_backtest,
)
from trading_bot_lab.backtesting import engine as engine_module
from trading_bot_lab.domain import (
    DataValidationError,
    ExecutionPhase,
    ExecutionTiming,
    MarketBar,
    RiskReason,
    Signal,
)
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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fast_window": 1.5, "slow_window": 3},
        {"fast_window": True, "slow_window": 3},
        {"fast_window": 1, "slow_window": False},
        {"fast_window": 1, "slow_window": 3, "target_weight": True},
    ],
)
def test_moving_average_configuration_is_strictly_typed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MovingAverageStrategy(**kwargs)


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
    assert first.fill.execution_phase is ExecutionPhase.OPEN


def test_final_bar_signal_expires_without_an_intent_or_fill() -> None:
    result = run_backtest(
        make_bars([100]),
        strategy=TargetSequenceStrategy((0.1,)),
        policy=policy(),
        config=permissive_config(),
    )

    assert result.equity_curve[-1].target_weight_for_next_bar == 0.1
    assert result.order_intents == ()
    assert result.fills == ()


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
    assert second_average == pytest.approx(104.5273631838618)
    assert result.summary.realized_pnl == pytest.approx(1_413.6363636)
    assert result.summary.ending_equity == pytest.approx(101_413.6363636)


def test_fee_only_erosion_is_accounted_once() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(fee_bps=10),
    )

    assert result.fills[0].quantity == pytest.approx(99.99000099)
    assert result.summary.total_fees_paid == pytest.approx(19.9980002)
    assert result.summary.realized_pnl == 0
    assert result.summary.ending_equity == pytest.approx(99_980.0019998)
    assert result.summary.total_fees_paid == pytest.approx(sum(fill.fee for fill in result.fills))


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

    assert result.fills[0].execution_price == pytest.approx(100.1)
    assert result.fills[1].execution_price == pytest.approx(99.9)
    assert result.summary.estimated_slippage_cost == pytest.approx(19.98001998)
    assert result.summary.realized_pnl == pytest.approx(-19.98001998)
    assert result.summary.ending_equity == pytest.approx(99_980.01998002)


def test_fee_and_bidirectional_slippage_are_each_charged_once() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(fee_bps=10, slippage_bps=10),
    )

    buy, sell = result.fills
    assert buy.quantity == sell.quantity == pytest.approx(99.9000999)
    assert (buy.execution_price, sell.execution_price) == pytest.approx((100.1, 99.9))
    assert result.summary.total_fees_paid == pytest.approx(19.98001998)
    assert result.summary.estimated_slippage_cost == pytest.approx(19.98001998)
    assert result.summary.realized_pnl == pytest.approx(-19.98001998)
    assert result.summary.ending_equity == pytest.approx(99_960.03996004)
    assert result.summary.max_exposure <= 0.1


def test_adverse_execution_notional_stays_within_order_cap() -> None:
    result = run_backtest(
        make_bars([100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1)),
        policy=policy(),
        config=permissive_config(slippage_bps=100),
    )

    fill = result.fills[0]
    assert fill.quantity * fill.execution_price <= 10_000 + 1e-6
    assert result.risk_decisions[-1].metrics["order_weight"] <= 0.1
    assert result.summary.max_exposure <= 0.1


def test_multi_fill_float_ledger_stays_reconciled_at_supported_precision() -> None:
    result = run_backtest(
        make_bars([3.33] * 7),
        strategy=TargetSequenceStrategy((0.1, 0.05, 0.08, 0.03, 0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(minimum_fee=0.006, slippage_bps=1),
    )

    assert result.summary.total_fees_paid == pytest.approx(
        sum(fill.fee for fill in result.fills),
        abs=1e-8,
    )
    for point in result.equity_curve:
        expected = (
            result.initial_equity
            + point.realized_pnl
            + point.unrealized_pnl
            - point.cumulative_fees
        )
        assert point.equity == pytest.approx(expected, abs=1e-5)


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


def test_daily_loss_accumulates_across_intraday_bars() -> None:
    start = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    timestamps = [start + timedelta(hours=index) for index in range(3)]
    result = run_backtest(
        make_bars(
            [100, 90, 80],
            opens=[100, 100, 90],
            timestamps=timestamps,
            timeframe_seconds=3_600,
        ),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=0.02,
            max_drawdown_pct=1.0,
        ),
        config=BacktestConfig(max_daily_loss_pct=0.02, max_drawdown_pct=1.0),
    )

    assert [point.start_of_day_equity for point in result.equity_curve] == [
        100_000,
        100_000,
        100_000,
    ]
    assert result.equity_curve[-1].daily_pnl == pytest.approx(-2_100)
    assert RiskReason.DAILY_LOSS in result.halt_state.reasons


def test_daily_start_resets_only_at_a_utc_date_boundary() -> None:
    timestamps = [
        datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 23, 0, tzinfo=UTC),
        datetime(2024, 1, 3, 0, 30, tzinfo=UTC),
    ]
    result = run_backtest(
        make_bars(
            [100, 90, 90],
            opens=[100, 100, 90],
            timestamps=timestamps,
            timeframe_seconds=3_600,
        ),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=0.015,
            max_drawdown_pct=1.0,
        ),
        config=BacktestConfig(max_daily_loss_pct=0.015, max_drawdown_pct=1.0),
    )

    assert result.equity_curve[1].daily_pnl == pytest.approx(-1_000)
    assert result.equity_curve[2].start_of_day_equity == pytest.approx(99_000)
    assert result.equity_curve[2].daily_pnl == 0
    assert not result.halt_state.active


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


def test_opening_equity_peak_persists_into_end_of_bar_drawdown() -> None:
    result = run_backtest(
        make_bars([100, 100, 100], opens=[100, 100, 200]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=1.0,
            max_drawdown_pct=0.049,
        ),
        config=BacktestConfig(max_daily_loss_pct=1.0, max_drawdown_pct=0.049),
    )

    assert result.equity_curve[-1].peak_equity == pytest.approx(110_000)
    assert result.equity_curve[-1].equity == pytest.approx(104_500)
    assert result.equity_curve[-1].drawdown == pytest.approx(0.05)
    assert RiskReason.MAX_DRAWDOWN in result.halt_state.reasons


def test_post_fill_loss_check_can_halt_before_any_later_bar() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1, 0.1)),
        policy=RiskPolicy(
            allowed_symbols=("SPY",),
            max_daily_loss_pct=0.00004,
            max_drawdown_pct=1.0,
        ),
        config=BacktestConfig(
            minimum_fee=5,
            max_daily_loss_pct=0.00004,
            max_drawdown_pct=1.0,
        ),
    )

    assert len(result.fills) == 1
    assert result.halt_state.timestamp == result.fills[0].timestamp
    assert RiskReason.DAILY_LOSS in result.halt_state.reasons
    assert result.equity_curve[-1].quantity == result.equity_curve[1].quantity


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


def test_buy_and_hold_drawdown_includes_first_open_to_close_loss() -> None:
    result = run_backtest(
        make_bars([50], opens=[100]),
        strategy=NoTradeStrategy(),
        policy=policy(),
    )

    assert result.benchmarks.buy_and_hold.ending_equity == 50_000
    assert result.benchmarks.buy_and_hold.max_drawdown == pytest.approx(0.5)


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


def test_zero_quantity_precision_keeps_a_one_share_order() -> None:
    result = run_backtest(
        make_bars([100, 100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.0, 0.0)),
        policy=policy(),
        config=permissive_config(initial_cash=1_000, quantity_precision=0),
    )

    assert [fill.quantity for fill in result.fills] == [1, 1]
    assert result.equity_curve[-1].quantity == 0


def test_risk_evaluator_exception_rejects_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated risk failure")

    monkeypatch.setattr(engine_module, "evaluate_order", explode)
    result = run_backtest(
        make_bars([100, 100]),
        strategy=TargetSequenceStrategy((0.1, 0.1)),
        policy=policy(),
        config=permissive_config(),
    )

    assert result.fills == ()
    assert result.equity_curve[-1].cash == 100_000
    assert result.risk_rejections[0].reasons == (RiskReason.RISK_EVALUATION_ERROR,)


def test_strategy_cannot_mutate_engine_owned_portfolio_state() -> None:
    class MutatingStrategy:
        name = "mutating_strategy"
        engine: SimulationEngine | None = None

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            assert self.engine is not None
            self.engine._cash = 0
            latest = history[-1]
            return Signal(latest.timestamp, latest.symbol, 0.1, self.name)

    strategy = MutatingStrategy()
    bars = make_bars([100])
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy(),
        config=permissive_config(),
        validated_bars=bars,
    )
    strategy.engine = engine

    with pytest.raises(RuntimeError, match="mutate protected engine state"):
        engine.process_bar(bars[0])

    assert engine.portfolio_state.cash == 100_000
    assert engine.portfolio_state.position.quantity == 0


def test_strategy_name_property_cannot_bypass_state_guard() -> None:
    class NameMutationStrategy:
        engine: SimulationEngine | None = None
        name_reads = 0

        @property
        def name(self) -> str:
            self.name_reads += 1
            if self.engine is not None:
                self.engine._cash = 1
            return "name_mutation"

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            latest = history[-1]
            return Signal(latest.timestamp, latest.symbol, 0.0, "name_mutation")

    strategy = NameMutationStrategy()
    bars = make_bars([100])
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy(),
        config=permissive_config(),
        validated_bars=bars,
    )
    strategy.engine = engine

    point = engine.process_bar(bars[0])

    assert strategy.name_reads == 1
    assert point.cash == point.equity == engine.portfolio_state.cash == 100_000


def test_strategy_cannot_mutate_existing_risk_metrics() -> None:
    class MetricsMutationStrategy:
        engine: SimulationEngine | None = None
        calls = 0

        @property
        def name(self) -> str:
            return "metrics_mutation"

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            self.calls += 1
            latest = history[-1]
            if self.calls == 2:
                assert self.engine is not None
                self.engine._risk_decisions[0].metrics["asset_weight"] = 999
            return Signal(latest.timestamp, latest.symbol, 0.1, self.name)

    strategy = MetricsMutationStrategy()
    first, second = make_bars([100, 100])
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy(),
        config=permissive_config(),
        validated_bars=(first, second),
    )
    strategy.engine = engine
    engine.process_bar(first)

    with pytest.raises(RuntimeError, match="mutate protected engine state"):
        engine.process_bar(second)

    assert engine.bars_processed == 1
    assert engine.portfolio_state.cash == 100_000


def test_strategy_cannot_disable_the_engine_event_sink() -> None:
    class SinkMutationStrategy:
        engine: SimulationEngine | None = None
        name = "sink_mutation"

        def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
            assert self.engine is not None
            self.engine._event_sink = None
            latest = history[-1]
            return Signal(latest.timestamp, latest.symbol, 0.0, self.name)

    events: list[dict[str, object]] = []
    sink = events.append
    strategy = SinkMutationStrategy()
    bars = make_bars([100])
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy(),
        config=permissive_config(),
        validated_bars=bars,
        event_sink=sink,
    )
    strategy.engine = engine

    with pytest.raises(RuntimeError, match="mutate protected engine state"):
        engine.process_bar(bars[0])

    assert engine._event_sink is sink


def test_mixed_timeframes_are_rejected_before_simulation() -> None:
    first = make_bars([100], timeframe_seconds=60)[0]
    second = MarketBar(
        timestamp=first.timestamp + timedelta(minutes=5),
        symbol="SPY",
        open=101,
        high=102,
        low=100,
        close=101,
        timeframe_seconds=300,
    )

    with pytest.raises(DataValidationError, match="consistent timeframe"):
        run_backtest((first, second), strategy=NoTradeStrategy(), policy=policy())


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


def test_backtest_config_rejects_bool_and_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="initial_cash"):
        BacktestConfig(initial_cash=True)
    with pytest.raises(ValueError, match="fee_bps"):
        BacktestConfig(fee_bps=float("nan"))
    with pytest.raises(ValueError, match="quantity_precision"):
        BacktestConfig(quantity_precision=True)
    with pytest.raises(ValueError, match="money_precision"):
        BacktestConfig(money_precision=7)
    with pytest.raises(ValueError, match="representable"):
        BacktestConfig(initial_cash=100.123456789, money_precision=8)


def test_only_next_bar_open_execution_timing_is_supported() -> None:
    assert BacktestConfig().execution_timing is ExecutionTiming.NEXT_BAR_OPEN
    with pytest.raises(ValueError, match="supported execution model"):
        BacktestConfig(execution_timing="same_bar_close")  # type: ignore[arg-type]


def test_signal_normalizes_timestamp_and_symbol_at_the_domain_boundary() -> None:
    signal = Signal(
        timestamp=datetime.fromisoformat("2024-01-01T02:00:00+02:00"),
        symbol="spy",
        target_weight=0.1,
        strategy_name=" baseline ",
    )

    assert signal.timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert signal.symbol == "SPY"
    assert signal.strategy_name == "baseline"
    with pytest.raises(ValueError, match="target_weight"):
        Signal(signal.timestamp, "SPY", True, "baseline")
