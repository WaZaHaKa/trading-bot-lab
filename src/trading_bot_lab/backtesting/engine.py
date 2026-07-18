"""Deterministic event-driven simulation engine.

Signals are generated after bar N closes and are eligible only at bar N+1's
open.  The same engine is used by batch backtests and local historical paper
replay so their signal, risk, fill, and accounting semantics cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from math import floor, isclose, isfinite

from trading_bot_lab import __version__
from trading_bot_lab.backtesting.moving_average import MovingAverageStrategy
from trading_bot_lab.domain import (
    BacktestResult,
    BacktestSummary,
    BenchmarkComparison,
    BenchmarkResult,
    DataValidationError,
    DataWarning,
    EquityPoint,
    ExecutionTiming,
    Fill,
    HaltState,
    MarketBar,
    MarketDataMetadata,
    OrderIntent,
    OrderSide,
    PortfolioState,
    Position,
    RiskDecisionRecord,
    RiskReason,
    RiskStatus,
    Signal,
    Strategy,
    Trade,
)
from trading_bot_lab.risk import (
    OrderRequest,
    PortfolioSnapshot,
    RiskPolicy,
    evaluate_order,
    evaluate_portfolio_halt,
)

ENGINE_VERSION = f"trading-bot-lab/{__version__}"
EventSink = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class BacktestConfig:
    """Typed simulation, cost, precision, and safety assumptions."""

    initial_cash: float = 100_000.0
    fee_bps: float = 0.0
    minimum_fee: float = 0.0
    slippage_bps: float = 0.0
    max_position_pct: float = 0.10
    max_total_exposure_pct: float = 0.30
    max_order_notional_pct: float = 0.10
    max_daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.05
    max_open_positions: int = 1
    warmup_bars: int = 0
    data_age_seconds: int = 0
    trading_enabled: bool = True
    kill_switch_active: bool = False
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_BAR_OPEN
    quantity_precision: int = 8
    money_precision: int = 8

    def __post_init__(self) -> None:
        _require_positive(self.initial_cash, "initial_cash")
        for value, name in (
            (self.fee_bps, "fee_bps"),
            (self.minimum_fee, "minimum_fee"),
            (self.slippage_bps, "slippage_bps"),
        ):
            _require_non_negative(value, name)
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be less than 10000")
        for value, name in (
            (self.max_position_pct, "max_position_pct"),
            (self.max_total_exposure_pct, "max_total_exposure_pct"),
            (self.max_order_notional_pct, "max_order_notional_pct"),
            (self.max_daily_loss_pct, "max_daily_loss_pct"),
            (self.max_drawdown_pct, "max_drawdown_pct"),
        ):
            _require_fraction(value, name)
        if self.max_position_pct > self.max_total_exposure_pct:
            raise ValueError("max_position_pct cannot exceed max_total_exposure_pct")
        if self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        if self.warmup_bars < 0:
            raise ValueError("warmup_bars must be non-negative")
        if self.data_age_seconds < 0:
            raise ValueError("data_age_seconds must be non-negative")
        if self.execution_timing is not ExecutionTiming.NEXT_BAR_OPEN:
            raise ValueError("only next_bar_open execution is supported")
        if not 0 <= self.quantity_precision <= 12:
            raise ValueError("quantity_precision must be between 0 and 12")
        if not 0 <= self.money_precision <= 12:
            raise ValueError("money_precision must be between 0 and 12")


class SimulationEngine:
    """Incremental deterministic engine with a read-only strategy boundary."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        policy: RiskPolicy,
        config: BacktestConfig,
        metadata: MarketDataMetadata | None = None,
        warnings: Sequence[DataWarning] = (),
        session_id: str | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config
        self.policy = _effective_policy(policy, config)
        self.metadata = metadata
        self.warnings = tuple(warnings)
        self.session_id = session_id or _derive_session_id(strategy, self.policy, config, metadata)
        self._event_sink = event_sink

        self._symbol: str | None = None
        self._cash = config.initial_cash
        self._quantity = 0.0
        self._average_cost = 0.0
        self._realized_pnl = 0.0
        self._cumulative_fees = 0.0
        self._cumulative_slippage = 0.0
        self._peak_equity = config.initial_cash
        self._previous_close_equity = config.initial_cash
        self._pending_signal: Signal | None = None
        self._last_intent_id: str | None = None
        self._halt_state = HaltState()
        self._history: list[MarketBar] = []
        self._equity_curve: list[EquityPoint] = []
        self._order_intents: list[OrderIntent] = []
        self._risk_decisions: list[RiskDecisionRecord] = []
        self._fills: list[Fill] = []
        self._trades: list[Trade] = []

    @property
    def bars_processed(self) -> int:
        return len(self._history)

    @property
    def halted(self) -> bool:
        return self._halt_state.active

    @property
    def portfolio_state(self) -> PortfolioState:
        mark = self._history[-1].close if self._history else 0.0
        equity = self._cash + self._quantity * mark
        return PortfolioState(
            cash=self._cash,
            position=Position(self._symbol or "", self._quantity, self._average_cost),
            realized_pnl=self._realized_pnl,
            cumulative_fees=self._cumulative_fees,
            cumulative_slippage=self._cumulative_slippage,
            equity=equity,
            peak_equity=self._peak_equity,
            start_of_day_equity=self._previous_close_equity,
        )

    def activate_kill_switch(self, timestamp) -> None:
        """Latch the manual kill-switch circuit breaker."""

        self._latch_halt(timestamp, (RiskReason.KILL_SWITCH,))

    def process_bar(self, bar: MarketBar) -> EquityPoint:
        """Process exactly one new bar without exposing future rows."""

        self._validate_event(bar)
        self._symbol = bar.symbol

        needs_open = self._quantity > 0 or (
            self._pending_signal is not None
            and not isclose(self._pending_signal.target_weight, 0.0, abs_tol=1e-12)
        )
        if needs_open and bar.open is None:
            raise DataValidationError(
                f"bar {bar.timestamp.isoformat()} requires open for next_bar_open execution"
            )
        open_mark = bar.open if bar.open is not None else bar.close
        equity_at_open = self._money(self._cash + self._quantity * open_mark)
        peak_at_open = max(self._peak_equity, equity_at_open)
        self._check_and_latch_portfolio_halt(
            timestamp=bar.timestamp,
            equity=equity_at_open,
            peak_equity=peak_at_open,
        )

        if self._pending_signal is not None and not self._halt_state.active:
            self._apply_pending_target(bar, open_mark, equity_at_open)

        position_market_value = self._money(self._quantity * bar.close)
        equity = self._money(self._cash + position_market_value)
        self._peak_equity = max(self._peak_equity, equity)
        self._check_and_latch_portfolio_halt(
            timestamp=bar.timestamp,
            equity=equity,
            peak_equity=self._peak_equity,
        )
        exposure_pct = position_market_value / equity if equity > 0 else 0.0
        unrealized_pnl = self._money(self._quantity * (bar.close - self._average_cost))
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        )

        self._history.append(bar)
        if len(self._history) <= self.config.warmup_bars or self._halt_state.active:
            next_signal = Signal(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                target_weight=0.0,
                strategy_name=self.strategy.name,
            )
        else:
            next_signal = self.strategy.signal_for_history(tuple(self._history))
            if next_signal.timestamp != bar.timestamp or next_signal.symbol != bar.symbol:
                raise DataValidationError(
                    "strategy signal must match the latest supplied bar timestamp and symbol"
                )
        self._pending_signal = next_signal

        point = EquityPoint(
            timestamp=bar.timestamp,
            close=bar.close,
            cash=self._cash,
            quantity=self._quantity,
            average_cost=self._average_cost,
            position_market_value=position_market_value,
            equity=equity,
            exposure_pct=exposure_pct,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized_pnl,
            cumulative_fees=self._cumulative_fees,
            cumulative_slippage=self._cumulative_slippage,
            drawdown=drawdown,
            halt_state=self._halt_state,
            target_weight_for_next_bar=next_signal.target_weight,
        )
        self._equity_curve.append(point)
        self._previous_close_equity = equity
        self._emit_bar_event(bar, next_signal, point)
        return point

    def finish(self) -> BacktestResult:
        """Build an immutable result after at least one processed bar."""

        if not self._history or self._symbol is None:
            raise ValueError("cannot finish a simulation before processing a bar")
        metadata = MarketDataMetadata(
            source=self.metadata.source if self.metadata is not None else "in_memory",
            symbol=self._symbol,
            row_count=len(self._history),
            start_timestamp=self._history[0].timestamp,
            end_timestamp=self._history[-1].timestamp,
            timeframe_seconds=(
                self.metadata.timeframe_seconds
                if self.metadata is not None
                else self._history[0].timeframe_seconds
            ),
        )
        summary = _build_summary(
            config=self.config,
            equity_curve=self._equity_curve,
            fills=self._fills,
            risk_decisions=self._risk_decisions,
            halt_state=self._halt_state,
            warning_count=len(self.warnings),
        )
        return BacktestResult(
            session_id=self.session_id,
            strategy_name=self.strategy.name,
            symbol=self._symbol,
            input_metadata=metadata,
            summary=summary,
            benchmarks=_build_benchmark_comparison(tuple(self._history), self.config.initial_cash),
            equity_curve=tuple(self._equity_curve),
            order_intents=tuple(self._order_intents),
            risk_decisions=tuple(self._risk_decisions),
            fills=tuple(self._fills),
            trades=tuple(self._trades),
            halt_state=self._halt_state,
            warnings=self.warnings,
        )

    def _validate_event(self, bar: MarketBar) -> None:
        if self._symbol is not None and bar.symbol != self._symbol:
            raise DataValidationError("simulation bars must contain exactly one symbol")
        if self._history and bar.timestamp <= self._history[-1].timestamp:
            raise DataValidationError(
                "simulation bars must be strictly ascending without duplicates"
            )

    def _apply_pending_target(
        self,
        bar: MarketBar,
        reference_price: float,
        equity: float,
    ) -> None:
        signal = self._pending_signal
        if signal is None:
            return
        target_value = signal.target_weight * equity
        raw_target_quantity = target_value / reference_price
        target_quantity = (
            self._floor_quantity(raw_target_quantity)
            if raw_target_quantity >= self._quantity
            else self._quantity_value(raw_target_quantity)
        )
        delta_quantity = self._quantity_value(target_quantity - self._quantity)
        if isclose(delta_quantity, 0.0, abs_tol=10 ** (-self.config.quantity_precision)):
            return

        side = OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL
        quantity = abs(delta_quantity)
        execution_price = _execution_price(reference_price, side, self.config.slippage_bps)
        execution_notional = quantity * execution_price
        reference_notional = quantity * reference_price
        fee = _trade_fee(execution_notional, self.config)
        slippage = quantity * abs(execution_price - reference_price)
        resulting_quantity = self._quantity_value(self._quantity + delta_quantity)
        resulting_exposure = resulting_quantity * reference_price
        current_exposure = self._quantity * reference_price
        intent_id = (
            f"{signal.timestamp.date().isoformat()}:{bar.symbol}:"
            f"{signal.target_weight:.12f}:{bar.timestamp.isoformat()}"
        )
        intent = OrderIntent(
            intent_id=intent_id,
            signal_timestamp=signal.timestamp,
            execution_timestamp=bar.timestamp,
            symbol=bar.symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            estimated_execution_price=execution_price,
            estimated_fee=fee,
            target_weight=signal.target_weight,
        )
        self._order_intents.append(intent)

        snapshot = self._portfolio_snapshot(equity)
        decision = evaluate_order(
            self.policy,
            OrderRequest(
                symbol=bar.symbol,
                side=side.value,
                quantity=quantity,
                reference_price=reference_price,
                execution_price=execution_price,
                notional=reference_notional,
                estimated_fee=fee,
                cash_required=execution_notional + fee,
                available_cash=self._cash,
                current_symbol_exposure=current_exposure,
                resulting_symbol_exposure=resulting_exposure,
                resulting_total_gross_exposure=max(resulting_exposure, 0.0),
                resulting_quantity=resulting_quantity,
                data_age_seconds=self.config.data_age_seconds,
                open_positions=1 if self._quantity > 0 else 0,
                intent_id=intent_id,
                last_intent_id=self._last_intent_id,
                data_valid=True,
                reduces_risk=resulting_exposure < current_exposure,
                is_live_order=False,
            ),
            snapshot,
        )
        record = RiskDecisionRecord(
            timestamp=bar.timestamp,
            status=decision.status,
            reasons=decision.reasons,
            metrics=decision.metrics,
            intent_id=intent_id,
        )
        self._risk_decisions.append(record)
        self._last_intent_id = intent_id
        self._emit(
            "risk_decision",
            bar,
            signal=asdict(signal),
            order_intent=asdict(intent),
            risk_decision=asdict(record),
        )
        if not decision.approved:
            return

        cash_required = execution_notional + fee
        if side is OrderSide.BUY and cash_required - self._cash > 1e-8:
            raise RuntimeError("risk engine approved an order without sufficient cash")

        realized_delta = 0.0
        if side is OrderSide.BUY:
            total_cost = self._quantity * self._average_cost + quantity * execution_price
            new_quantity = self._quantity_value(self._quantity + quantity)
            new_average_cost = total_cost / new_quantity if new_quantity > 0 else 0.0
            self._cash = self._money(self._cash - execution_notional - fee)
            self._quantity = new_quantity
            self._average_cost = self._money(new_average_cost)
        else:
            if quantity - self._quantity > 1e-8:
                raise RuntimeError("risk engine approved a short sale")
            realized_delta = self._money(quantity * (execution_price - self._average_cost))
            self._realized_pnl = self._money(self._realized_pnl + realized_delta)
            self._cash = self._money(self._cash + execution_notional - fee)
            self._quantity = self._quantity_value(self._quantity - quantity)
            if isclose(self._quantity, 0.0, abs_tol=10 ** (-self.config.quantity_precision)):
                self._quantity = 0.0
                self._average_cost = 0.0

        self._cumulative_fees = self._money(self._cumulative_fees + fee)
        self._cumulative_slippage = self._money(self._cumulative_slippage + slippage)
        if self._cash < -1e-8 or self._quantity < -1e-8:
            raise RuntimeError("approved fill violated cash or long-only invariants")

        fill = Fill(
            intent_id=intent_id,
            timestamp=bar.timestamp,
            symbol=bar.symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            execution_price=execution_price,
            fee=fee,
            slippage_cost=slippage,
        )
        trade = Trade(
            fill=fill,
            average_cost_after=self._average_cost,
            realized_pnl_delta=realized_delta,
            target_weight=signal.target_weight,
        )
        self._fills.append(fill)
        self._trades.append(trade)
        self._emit(
            "fill",
            bar,
            signal=asdict(signal),
            order_intent=asdict(intent),
            risk_decision=asdict(record),
            fill=asdict(fill),
        )

    def _portfolio_snapshot(self, equity: float) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            equity=equity,
            start_of_day_equity=max(self._previous_close_equity, 1e-12),
            peak_equity=max(self._peak_equity, equity),
            daily_pnl=equity - self._previous_close_equity,
            trading_enabled=self.config.trading_enabled,
            kill_switch_active=self.config.kill_switch_active,
            cash=self._cash,
            open_positions=1 if self._quantity > 0 else 0,
            halted=self._halt_state.active,
        )

    def _check_and_latch_portfolio_halt(
        self,
        *,
        timestamp,
        equity: float,
        peak_equity: float,
    ) -> None:
        if self._halt_state.active:
            return
        snapshot = PortfolioSnapshot(
            equity=equity,
            start_of_day_equity=max(self._previous_close_equity, 1e-12),
            peak_equity=max(peak_equity, equity),
            daily_pnl=equity - self._previous_close_equity,
            trading_enabled=self.config.trading_enabled,
            kill_switch_active=self.config.kill_switch_active,
            cash=self._cash,
            open_positions=1 if self._quantity > 0 else 0,
        )
        decision = evaluate_portfolio_halt(self.policy, snapshot)
        if decision.approved:
            return
        self._risk_decisions.append(
            RiskDecisionRecord(
                timestamp=timestamp,
                status=decision.status,
                reasons=decision.reasons,
                metrics=decision.metrics,
            )
        )
        self._latch_halt(timestamp, decision.reasons)

    def _latch_halt(self, timestamp, reasons: tuple[RiskReason, ...]) -> None:
        if self._halt_state.active:
            return
        self._halt_state = HaltState(True, timestamp, reasons)
        bar = self._history[-1] if self._history else None
        if bar is not None:
            self._emit("halt", bar, halt_state=asdict(self._halt_state))

    def _emit_bar_event(self, bar: MarketBar, signal: Signal, point: EquityPoint) -> None:
        self._emit(
            "bar_processed",
            bar,
            signal=asdict(signal),
            position={"quantity": point.quantity, "average_cost": point.average_cost},
            equity=point.equity,
            drawdown=point.drawdown,
            halt_state=asdict(point.halt_state),
        )

    def _emit(self, event: str, bar: MarketBar, **details: object) -> None:
        if self._event_sink is None:
            return
        payload: dict[str, object] = {
            "event": event,
            "session_id": self.session_id,
            "strategy_name": self.strategy.name,
            "symbol": bar.symbol,
            "event_timestamp": bar.timestamp.isoformat(),
        }
        payload.update(details)
        self._event_sink(payload)

    def _money(self, value: float) -> float:
        return round(value, self.config.money_precision)

    def _quantity_value(self, value: float) -> float:
        return round(value, self.config.quantity_precision)

    def _floor_quantity(self, value: float) -> float:
        scale = 10**self.config.quantity_precision
        return floor(value * scale) / scale


def run_backtest(
    bars: Sequence[MarketBar],
    *,
    strategy: Strategy,
    policy: RiskPolicy | None = None,
    config: BacktestConfig | None = None,
    metadata: MarketDataMetadata | None = None,
    warnings: Sequence[DataWarning] = (),
    session_id: str | None = None,
    event_sink: EventSink | None = None,
) -> BacktestResult:
    """Run a deterministic batch simulation over a validated bar sequence."""

    if not bars:
        raise ValueError("bars must not be empty")
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy or RiskPolicy(allowed_symbols=(bars[0].symbol,)),
        config=config or BacktestConfig(),
        metadata=metadata,
        warnings=warnings,
        session_id=session_id,
        event_sink=event_sink,
    )
    for bar in bars:
        engine.process_bar(bar)
    return engine.finish()


def run_moving_average_backtest(
    bars: Sequence[MarketBar],
    *,
    strategy: MovingAverageStrategy | None = None,
    policy: RiskPolicy | None = None,
    config: BacktestConfig | None = None,
    metadata: MarketDataMetadata | None = None,
    warnings: Sequence[DataWarning] = (),
    event_sink: EventSink | None = None,
) -> BacktestResult:
    """Backward-compatible moving-average backtest entry point."""

    selected = strategy or MovingAverageStrategy()
    return run_backtest(
        bars,
        strategy=selected,
        policy=policy,
        config=config,
        metadata=metadata,
        warnings=warnings,
        event_sink=event_sink,
    )


def _effective_policy(policy: RiskPolicy, config: BacktestConfig) -> RiskPolicy:
    return replace(
        policy,
        max_asset_weight=min(policy.max_asset_weight, config.max_position_pct),
        max_total_gross_exposure=min(
            policy.max_total_gross_exposure,
            config.max_total_exposure_pct,
        ),
        max_order_notional_weight=min(
            policy.max_order_notional_weight,
            config.max_order_notional_pct,
        ),
        max_daily_loss_pct=min(policy.max_daily_loss_pct, config.max_daily_loss_pct),
        max_drawdown_pct=min(policy.max_drawdown_pct, config.max_drawdown_pct),
        max_open_positions=min(policy.max_open_positions, config.max_open_positions),
    )


def _execution_price(reference_price: float, side: OrderSide, slippage_bps: float) -> float:
    slippage = slippage_bps / 10_000
    multiplier = 1 + slippage if side is OrderSide.BUY else 1 - slippage
    return reference_price * multiplier


def _trade_fee(execution_notional: float, config: BacktestConfig) -> float:
    return max(execution_notional * (config.fee_bps / 10_000), config.minimum_fee)


def _build_summary(
    *,
    config: BacktestConfig,
    equity_curve: Sequence[EquityPoint],
    fills: Sequence[Fill],
    risk_decisions: Sequence[RiskDecisionRecord],
    halt_state: HaltState,
    warning_count: int,
) -> BacktestSummary:
    ending = equity_curve[-1]
    exposures = [point.exposure_pct for point in equity_curve]
    rejected = sum(
        decision.status is RiskStatus.REJECTED and decision.intent_id is not None
        for decision in risk_decisions
    )
    traded_notional = sum(fill.quantity * fill.execution_price for fill in fills)
    return BacktestSummary(
        start_timestamp=equity_curve[0].timestamp,
        end_timestamp=ending.timestamp,
        starting_cash=config.initial_cash,
        ending_equity=ending.equity,
        total_return=(ending.equity / config.initial_cash) - 1.0,
        max_drawdown=max(point.drawdown for point in equity_curve),
        number_of_trades=len(fills),
        turnover=traded_notional / config.initial_cash,
        total_fees_paid=ending.cumulative_fees,
        estimated_slippage_cost=ending.cumulative_slippage,
        average_exposure=sum(exposures) / len(exposures),
        max_exposure=max(exposures),
        realized_pnl=ending.realized_pnl,
        unrealized_pnl=ending.unrealized_pnl,
        risk_halt_triggered=halt_state.active,
        rejected_order_count=rejected,
        warning_count=warning_count,
    )


def _build_benchmark_comparison(
    bars: tuple[MarketBar, ...],
    starting_cash: float,
) -> BenchmarkComparison:
    start_price = bars[0].open if bars[0].open is not None else bars[0].close
    quantity = starting_cash / start_price
    equity_values = [quantity * bar.close for bar in bars]
    ending_equity = equity_values[-1]
    buy_and_hold = BenchmarkResult(
        name="buy_and_hold",
        start_timestamp=bars[0].timestamp,
        end_timestamp=bars[-1].timestamp,
        starting_cash=starting_cash,
        ending_equity=ending_equity,
        total_return=(ending_equity / starting_cash) - 1.0,
        max_drawdown=_max_drawdown_from_values(equity_values),
    )
    cash = BenchmarkResult(
        name="cash",
        start_timestamp=bars[0].timestamp,
        end_timestamp=bars[-1].timestamp,
        starting_cash=starting_cash,
        ending_equity=starting_cash,
        total_return=0.0,
        max_drawdown=0.0,
    )
    return BenchmarkComparison(buy_and_hold=buy_and_hold, cash=cash)


def _max_drawdown_from_values(values: Sequence[float]) -> float:
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _derive_session_id(
    strategy: Strategy,
    policy: RiskPolicy,
    config: BacktestConfig,
    metadata: MarketDataMetadata | None,
) -> str:
    material = "|".join(
        (
            ENGINE_VERSION,
            strategy.name,
            repr(policy),
            repr(config),
            repr(metadata),
        )
    )
    return f"sim-{sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _require_positive(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def _require_non_negative(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")


def _require_fraction(value: float, name: str) -> None:
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and between 0 and 1")


BacktestTrade = Trade
BenchmarkSummary = BenchmarkResult
RiskRejection = RiskDecisionRecord


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestSummary",
    "BacktestTrade",
    "BenchmarkComparison",
    "BenchmarkSummary",
    "ENGINE_VERSION",
    "EquityPoint",
    "RiskRejection",
    "SimulationEngine",
    "run_backtest",
    "run_moving_average_backtest",
]
