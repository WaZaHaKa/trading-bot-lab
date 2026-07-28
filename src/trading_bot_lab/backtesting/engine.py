"""Deterministic event-driven simulation engine.

Signals are generated after bar N closes and are eligible only at bar N+1's
open.  The same engine is used by batch backtests and local historical paper
replay so their signal, risk, fill, and accounting semantics cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from hashlib import sha256
from math import isfinite

from trading_bot_lab import __version__
from trading_bot_lab.backtesting.moving_average import MovingAverageStrategy
from trading_bot_lab.domain import (
    BacktestConfig,
    BacktestResult,
    BacktestSummary,
    BenchmarkComparison,
    BenchmarkResult,
    DataValidationError,
    DataWarning,
    EquityPoint,
    ExecutionPhase,
    Fill,
    HaltState,
    MarketBar,
    MarketDataMetadata,
    OrderIntent,
    OrderSide,
    PortfolioState,
    Position,
    RiskConfiguration,
    RiskDecisionRecord,
    RiskReason,
    RiskStatus,
    Signal,
    Strategy,
    Trade,
    WarningCode,
)
from trading_bot_lab.provenance import bars_content_sha256
from trading_bot_lab.risk import (
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    RiskPolicy,
    evaluate_order,
    evaluate_portfolio_halt,
)

ENGINE_VERSION = f"trading-bot-lab/{__version__}"
EVENT_SCHEMA_VERSION = "1.0.0"
EventSink = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class _StrategyProtectedState:
    strategy: Strategy
    strategy_name: str
    strategy_configuration: tuple[tuple[str, str | int | float | bool], ...]
    config: BacktestConfig
    policy: RiskPolicy
    metadata: MarketDataMetadata | None
    warnings: tuple[DataWarning, ...]
    validated_bars: tuple[MarketBar, ...]
    bars_content_digest: str
    session_id: str
    event_sink: EventSink | None
    event_buffer: tuple[dict[str, object], ...] | None
    delivering_event: bool
    processing_failed: bool
    symbol: str | None
    cash: float
    quantity: float
    average_cost: float
    realized_pnl: float
    cumulative_fees: float
    cumulative_slippage: float
    peak_equity: float
    previous_close_equity: float
    start_of_day_equity: float
    current_utc_date: date | None
    pending_signal: Signal | None
    last_intent_id: str | None
    halt_state: HaltState
    history: tuple[MarketBar, ...]
    equity_curve: tuple[EquityPoint, ...]
    order_intents: tuple[OrderIntent, ...]
    risk_decisions: tuple[RiskDecisionRecord, ...]
    fills: tuple[Fill, ...]
    trades: tuple[Trade, ...]


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
        validated_bars: Sequence[MarketBar],
        event_sink: EventSink | None = None,
    ) -> None:
        _validate_builtin_strategy_history(strategy, config)
        strategy_name = strategy.name
        if not isinstance(strategy_name, str) or not strategy_name.strip():
            raise ValueError("strategy name must be a non-empty string")
        self.strategy = strategy
        self._strategy_name = strategy_name.strip()
        self._strategy_configuration = _validated_strategy_configuration(strategy)
        self.config = config
        self.policy = _effective_policy(policy, config)
        self.warnings = tuple(warnings)
        self._validated_bars = validate_simulation_bars(validated_bars, metadata=metadata)
        self.metadata = (
            build_market_data_metadata(self._validated_bars) if metadata is None else metadata
        )
        self._bars_content_digest = bars_content_sha256(self._validated_bars)
        self.session_id = _derive_session_id(
            self._strategy_name,
            self._strategy_configuration,
            self.policy,
            config,
            self.metadata,
            self._bars_content_digest,
        )
        self._event_sink = event_sink
        self._event_buffer: list[dict[str, object]] | None = None
        self._delivering_event = False
        self._processing_failed = False

        self._symbol: str | None = None
        self._cash = config.initial_cash
        self._quantity = 0.0
        self._average_cost = 0.0
        self._realized_pnl = 0.0
        self._cumulative_fees = 0.0
        self._cumulative_slippage = 0.0
        self._peak_equity = config.initial_cash
        self._previous_close_equity = config.initial_cash
        self._start_of_day_equity = config.initial_cash
        self._current_utc_date: date | None = None
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
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def strategy_configuration(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        return self._strategy_configuration

    @property
    def assumptions(self) -> BacktestConfig:
        return self.config

    @property
    def risk_configuration(self) -> RiskConfiguration:
        return _risk_configuration(self.policy)

    @property
    def input_metadata(self) -> MarketDataMetadata:
        assert self.metadata is not None
        return self.metadata

    @property
    def halt_state(self) -> HaltState:
        return self._halt_state

    @property
    def portfolio_state(self) -> PortfolioState:
        mark = self._history[-1].close if self._history else 0.0
        position_market_value = self._money(self._quantity * mark)
        equity = self._money(self._cash + position_market_value)
        return PortfolioState(
            cash=self._cash,
            position=Position(self._symbol or "", self._quantity, self._average_cost),
            realized_pnl=self._realized_pnl,
            cumulative_fees=self._cumulative_fees,
            cumulative_slippage=self._cumulative_slippage,
            equity=equity,
            peak_equity=self._peak_equity,
            start_of_day_equity=self._start_of_day_equity,
        )

    def activate_kill_switch(self, timestamp: datetime) -> None:
        """Latch the manual kill-switch circuit breaker."""

        self.expire_pending_signal(timestamp, "manual kill switch")
        self._latch_halt(timestamp, (RiskReason.KILL_SWITCH,))

    def expire_pending_signal(self, timestamp: datetime, reason: str) -> None:
        """Cancel a pending target so it cannot execute after a terminal control."""

        signal = self._pending_signal
        if signal is None:
            return
        self._pending_signal = None
        bar = self._history[-1] if self._history else None
        if bar is not None:
            self._emit(
                "pending_signal_expired",
                bar,
                event_timestamp=timestamp.isoformat(),
                signal_timestamp=signal.timestamp.isoformat(),
                target_weight=signal.target_weight,
                reason=reason,
            )

    def process_bar(self, bar: MarketBar) -> EquityPoint:
        """Process one bar atomically with respect to engine-owned state."""

        if self._processing_failed:
            raise RuntimeError("simulation engine cannot continue after a bar-processing failure")
        protected = self._capture_strategy_protected_state()
        self._event_buffer = []
        try:
            point = self._process_bar(bar)
        except Exception:
            self._restore_strategy_protected_state(protected)
            self._processing_failed = True
            raise
        pending_events = tuple(self._event_buffer)
        self._event_buffer = None
        for payload in pending_events:
            self.publish_event(payload)
        return point

    def publish_event(self, payload: dict[str, object]) -> None:
        """Buffer transactional events or deliver committed lifecycle events."""

        if self._event_buffer is not None:
            self._event_buffer.append(deepcopy(payload))
            return
        self._deliver_event(payload)

    def _deliver_event(self, payload: dict[str, object]) -> None:
        """Deliver one event while protecting all engine-owned state."""

        if self._event_sink is None:
            return
        if self._delivering_event:
            raise RuntimeError("structured event delivery cannot be reentrant")
        protected = self._capture_strategy_protected_state()
        self._delivering_event = True
        try:
            self._event_sink(payload)
        except Exception as error:
            self._delivering_event = False
            self._restore_strategy_protected_state(protected)
            self._record_event_sink_warning(
                "local structured event delivery failed with "
                f"{type(error).__name__}; simulation continued and the event may be missing"
            )
            return
        self._delivering_event = False
        try:
            state_changed = self._strategy_state_changed(protected)
        except Exception:
            self._restore_strategy_protected_state(protected)
            self._record_event_sink_warning(
                "local structured event sink left protected engine state invalid; "
                "the mutation was rolled back"
            )
            return
        if state_changed:
            self._restore_strategy_protected_state(protected)
            self._record_event_sink_warning(
                "local structured event sink mutated protected engine state; "
                "the mutation was rolled back"
            )

    def _record_event_sink_warning(self, message: str) -> None:
        if any(existing.code is WarningCode.EVENT_SINK_FAILURE for existing in self.warnings):
            return
        self.warnings = (
            *self.warnings,
            DataWarning(code=WarningCode.EVENT_SINK_FAILURE, message=message),
        )

    def _process_bar(self, bar: MarketBar) -> EquityPoint:
        """Process exactly one new bar without exposing future rows."""

        self._validate_event(bar)
        if bar.open is None:
            raise DataValidationError(
                f"bar {bar.timestamp.isoformat()} requires open for next_bar_open execution; "
                "close fallback is prohibited"
            )
        self._emit(
            "bar_received",
            bar,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        self._symbol = bar.symbol
        if self._current_utc_date is None or bar.timestamp.date() != self._current_utc_date:
            self._current_utc_date = bar.timestamp.date()
            self._start_of_day_equity = self._previous_close_equity

        open_mark = bar.open
        open_position_market_value = self._money(self._quantity * open_mark)
        equity_at_open = self._money(self._cash + open_position_market_value)
        self._peak_equity = max(self._peak_equity, equity_at_open)
        self._check_and_latch_portfolio_halt(
            bar=bar,
            timestamp=bar.timestamp,
            equity=equity_at_open,
            peak_equity=self._peak_equity,
        )

        if self._pending_signal is not None and not self._halt_state.active:
            self._apply_pending_target(bar, open_mark, equity_at_open)

        position_market_value = self._money(self._quantity * bar.close)
        equity = self._money(self._cash + position_market_value)
        self._peak_equity = max(self._peak_equity, equity)
        self._check_and_latch_portfolio_halt(
            bar=bar,
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
                strategy_name=self._strategy_name,
            )
        else:
            next_signal = self._call_strategy(
                tuple(self._history[-self.config.strategy_history_limit :])
            )
            if (
                next_signal.timestamp != bar.timestamp
                or next_signal.symbol != bar.symbol
                or next_signal.strategy_name != self._strategy_name
            ):
                raise DataValidationError(
                    "strategy signal must match the latest supplied bar, symbol, and strategy name"
                )
        self._pending_signal = next_signal
        self._emit(
            "signal_generated",
            bar,
            signal_timestamp=next_signal.timestamp.isoformat(),
            target_weight=next_signal.target_weight,
        )

        point = EquityPoint(
            timestamp=bar.timestamp,
            close=bar.close,
            cash=self._cash,
            quantity=self._quantity,
            average_cost=self._average_cost,
            position_market_value=position_market_value,
            equity=equity,
            start_of_day_equity=self._start_of_day_equity,
            daily_pnl=self._money(equity - self._start_of_day_equity),
            peak_equity=self._peak_equity,
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
        assert self.metadata is not None
        metadata = self.metadata
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
            strategy_name=self._strategy_name,
            strategy_configuration=self._strategy_configuration,
            symbol=self._symbol,
            input_metadata=metadata,
            assumptions=self.config,
            risk_configuration=_risk_configuration(self.policy),
            summary=summary,
            benchmarks=_build_benchmark_comparison(tuple(self._history), self.config),
            equity_curve=tuple(self._equity_curve),
            order_intents=tuple(self._order_intents),
            risk_decisions=tuple(
                replace(decision, metrics=dict(decision.metrics))
                for decision in self._risk_decisions
            ),
            fills=tuple(self._fills),
            trades=tuple(self._trades),
            halt_state=self._halt_state,
            warnings=self.warnings,
        )

    def _validate_event(self, bar: MarketBar) -> None:
        event_index = len(self._history)
        if event_index >= len(self._validated_bars):
            raise DataValidationError("simulation received more bars than validated input")
        if bar != self._validated_bars[event_index]:
            raise DataValidationError(
                "simulation bar does not match the validated input event sequence"
            )
        if self.metadata is not None:
            if bar.symbol != self.metadata.symbol:
                raise DataValidationError("simulation bar symbol conflicts with input metadata")
            if bar.timeframe_seconds != self.metadata.timeframe_seconds:
                raise DataValidationError("simulation bar timeframe conflicts with input metadata")
        if self._symbol is not None and bar.symbol != self._symbol:
            raise DataValidationError("simulation bars must contain exactly one symbol")
        if self._history and bar.timeframe_seconds != self._history[0].timeframe_seconds:
            raise DataValidationError("simulation bars must use one consistent timeframe")
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
        target_quantity = self._cost_aware_target_quantity(
            target_weight=signal.target_weight,
            reference_price=reference_price,
            equity=equity,
        )
        delta_quantity = self._quantity_value(target_quantity - self._quantity)
        if delta_quantity == 0.0:
            return

        side = OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL
        quantity = abs(delta_quantity)
        execution_price = _execution_price(reference_price, side, self.config.slippage_bps)
        execution_notional = quantity * execution_price
        cash_notional = self._money(execution_notional)
        fee = self._money(_trade_fee(execution_notional, self.config))
        slippage = self._money(quantity * abs(execution_price - reference_price))
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
        self._emit(
            "intent_created",
            bar,
            intent_id=intent.intent_id,
            signal_timestamp=intent.signal_timestamp.isoformat(),
            execution_timestamp=intent.execution_timestamp.isoformat(),
            side=intent.side.value,
            quantity=intent.quantity,
            reference_price=intent.reference_price,
            estimated_execution_price=intent.estimated_execution_price,
            estimated_fee=intent.estimated_fee,
            target_weight=intent.target_weight,
        )

        snapshot = self._portfolio_snapshot(equity)
        request = OrderRequest(
            symbol=bar.symbol,
            side=side.value,
            quantity=quantity,
            reference_price=reference_price,
            execution_price=execution_price,
            notional=execution_notional,
            estimated_fee=fee,
            cash_required=cash_notional + fee if side is OrderSide.BUY else None,
            available_cash=self._cash,
            current_quantity=self._quantity,
            current_symbol_exposure=current_exposure,
            current_total_gross_exposure=current_exposure,
            resulting_symbol_exposure=resulting_exposure,
            resulting_total_gross_exposure=max(resulting_exposure, 0.0),
            resulting_quantity=resulting_quantity,
            money_precision=self.config.money_precision,
            data_age_seconds=self.config.data_age_seconds,
            open_positions=1 if self._quantity > 0 else 0,
            intent_id=intent_id,
            last_intent_id=self._last_intent_id,
            data_valid=True,
            reduces_risk=(side is OrderSide.SELL and resulting_exposure < current_exposure),
            is_live_order=False,
        )
        try:
            decision = evaluate_order(self.policy, request, snapshot)
        except Exception:
            decision = RiskDecision(
                status=RiskStatus.REJECTED,
                reasons=(RiskReason.RISK_EVALUATION_ERROR,),
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
            "risk_accepted" if decision.approved else "risk_rejected",
            bar,
            intent_id=intent_id,
            risk_reasons=[reason.value for reason in record.reasons],
            risk_metrics=dict(sorted(record.metrics.items())),
        )
        if not decision.approved:
            return

        realized_delta = 0.0
        new_cash = self._cash
        new_quantity = self._quantity
        new_average_cost = self._average_cost
        new_realized_pnl = self._realized_pnl
        if side is OrderSide.BUY:
            total_cost = self._quantity * self._average_cost + quantity * execution_price
            new_quantity = self._quantity_value(self._quantity + quantity)
            new_average_cost = total_cost / new_quantity if new_quantity > 0 else 0.0
            new_cash = self._money(self._cash - cash_notional - fee)
        else:
            if quantity > self._quantity:
                raise RuntimeError("risk engine approved a short sale")
            realized_delta = self._money(quantity * (execution_price - self._average_cost))
            new_realized_pnl = self._money(self._realized_pnl + realized_delta)
            new_cash = self._money(self._cash + cash_notional - fee)
            new_quantity = self._quantity_value(self._quantity - quantity)
            if new_quantity == 0.0:
                new_average_cost = 0.0

        if new_cash < 0 or new_quantity < 0:
            raise RuntimeError("approved fill violated cash or long-only invariants")

        self._cash = new_cash
        self._quantity = new_quantity
        self._average_cost = new_average_cost
        self._realized_pnl = new_realized_pnl
        self._cumulative_fees = self._money(self._cumulative_fees + fee)
        self._cumulative_slippage = self._money(self._cumulative_slippage + slippage)

        fill = Fill(
            intent_id=intent_id,
            fill_id=("fill-" + sha256(f"{self.session_id}|{intent_id}".encode()).hexdigest()[:16]),
            timestamp=bar.timestamp,
            symbol=bar.symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            execution_price=execution_price,
            fee=fee,
            slippage_cost=slippage,
            execution_phase=ExecutionPhase.OPEN,
        )
        trade = Trade(
            fill=fill,
            signal_timestamp=signal.timestamp,
            average_cost_after=self._average_cost,
            realized_pnl_delta=realized_delta,
            resulting_cash=self._cash,
            resulting_quantity=self._quantity,
            target_weight=signal.target_weight,
        )
        self._fills.append(fill)
        self._trades.append(trade)
        self._emit(
            "fill_created",
            bar,
            intent_id=fill.intent_id,
            fill_id=fill.fill_id,
            side=fill.side.value,
            quantity=fill.quantity,
            reference_price=fill.reference_price,
            execution_price=fill.execution_price,
            fee=fill.fee,
            slippage_cost=fill.slippage_cost,
            resulting_cash=self._cash,
            resulting_quantity=self._quantity,
        )
        post_fill_market_value = self._money(self._quantity * reference_price)
        post_fill_equity = self._money(self._cash + post_fill_market_value)
        self._check_and_latch_portfolio_halt(
            bar=bar,
            timestamp=bar.timestamp,
            equity=post_fill_equity,
            peak_equity=self._peak_equity,
        )

    def _cost_aware_target_quantity(
        self,
        *,
        target_weight: float,
        reference_price: float,
        equity: float,
    ) -> float:
        """Return the largest precision-safe quantity within the post-cost target."""

        if target_weight == 0.0:
            return 0.0
        scale = 10**self.config.quantity_precision
        current_units = round(self._quantity * scale)
        raw_target_units = int((target_weight * equity / reference_price) * scale)
        current_market_value = self._money(self._quantity * reference_price)
        current_weight = current_market_value / equity
        if current_weight <= target_weight:
            low = current_units
            high = max(current_units, raw_target_units)
        else:
            low = 0
            high = current_units

        best_units: int | None = None
        while low <= high:
            candidate_units = (low + high) // 2
            candidate_quantity = candidate_units / scale
            delta = abs(candidate_quantity - self._quantity)
            candidate_market_value = self._money(candidate_quantity * reference_price)
            execution_notional = 0.0
            cash_notional = 0.0
            side: OrderSide | None = None
            if delta == 0.0:
                projected_cash = self._cash
            else:
                side = OrderSide.BUY if candidate_quantity > self._quantity else OrderSide.SELL
                execution_price = _execution_price(
                    reference_price,
                    side,
                    self.config.slippage_bps,
                )
                execution_notional = delta * execution_price
                cash_notional = self._money(execution_notional)
                fee = self._money(_trade_fee(execution_notional, self.config))
                if side is OrderSide.BUY:
                    projected_cash = self._money(self._cash - cash_notional - fee)
                else:
                    projected_cash = self._money(self._cash + cash_notional - fee)
            projected_equity = self._money(projected_cash + candidate_market_value)
            order_within_limit = bool(
                target_weight > self.config.max_order_notional_pct + 1e-12
                or side is not OrderSide.BUY
                or execution_notional / equity <= self.config.max_order_notional_pct + 1e-12
            )
            buy_has_representable_value = bool(
                side is not OrderSide.BUY or (candidate_market_value > 0 and cash_notional > 0)
            )
            candidate_is_safe = bool(
                projected_equity > 0
                and candidate_market_value / projected_equity <= target_weight + 1e-12
                and order_within_limit
                and buy_has_representable_value
            )
            if candidate_is_safe:
                best_units = candidate_units
                low = candidate_units + 1
            else:
                high = candidate_units - 1

        if best_units is None:
            return 0.0
        return best_units / scale

    def _call_strategy(self, history: tuple[MarketBar, ...]) -> Signal:
        """Call trusted strategy code while guarding engine-owned state."""

        protected = self._capture_strategy_protected_state()
        try:
            signal = self.strategy.signal_for_history(history)
        except Exception as exc:
            if self._strategy_state_changed(protected):
                self._restore_strategy_protected_state(protected)
                raise RuntimeError("strategy attempted to mutate protected engine state") from exc
            raise
        if self._strategy_state_changed(protected):
            self._restore_strategy_protected_state(protected)
            raise RuntimeError("strategy attempted to mutate protected engine state")
        if not isinstance(signal, Signal):
            raise DataValidationError("strategy must return a Signal")
        return signal

    def _capture_strategy_protected_state(self) -> _StrategyProtectedState:
        return _StrategyProtectedState(
            strategy=self.strategy,
            strategy_name=self._strategy_name,
            strategy_configuration=self._strategy_configuration,
            config=self.config,
            policy=self.policy,
            metadata=self.metadata,
            warnings=self.warnings,
            validated_bars=self._validated_bars,
            bars_content_digest=self._bars_content_digest,
            session_id=self.session_id,
            event_sink=self._event_sink,
            event_buffer=(
                None if self._event_buffer is None else tuple(deepcopy(self._event_buffer))
            ),
            delivering_event=self._delivering_event,
            processing_failed=self._processing_failed,
            symbol=self._symbol,
            cash=self._cash,
            quantity=self._quantity,
            average_cost=self._average_cost,
            realized_pnl=self._realized_pnl,
            cumulative_fees=self._cumulative_fees,
            cumulative_slippage=self._cumulative_slippage,
            peak_equity=self._peak_equity,
            previous_close_equity=self._previous_close_equity,
            start_of_day_equity=self._start_of_day_equity,
            current_utc_date=self._current_utc_date,
            pending_signal=self._pending_signal,
            last_intent_id=self._last_intent_id,
            halt_state=self._halt_state,
            history=tuple(self._history),
            equity_curve=tuple(self._equity_curve),
            order_intents=tuple(self._order_intents),
            risk_decisions=tuple(
                replace(decision, metrics=dict(decision.metrics))
                for decision in self._risk_decisions
            ),
            fills=tuple(self._fills),
            trades=tuple(self._trades),
        )

    def _strategy_state_changed(self, state: _StrategyProtectedState) -> bool:
        current = self._capture_strategy_protected_state()
        return (
            self.strategy is not state.strategy
            or replace(
                current,
                strategy=state.strategy,
            )
            != state
        )

    def _restore_strategy_protected_state(self, state: _StrategyProtectedState) -> None:
        self.strategy = state.strategy
        self._strategy_name = state.strategy_name
        self._strategy_configuration = state.strategy_configuration
        self.config = state.config
        self.policy = state.policy
        self.metadata = state.metadata
        self.warnings = state.warnings
        self._validated_bars = state.validated_bars
        self._bars_content_digest = state.bars_content_digest
        self.session_id = state.session_id
        self._event_sink = state.event_sink
        self._event_buffer = (
            None
            if state.event_buffer is None
            else [deepcopy(payload) for payload in state.event_buffer]
        )
        self._delivering_event = state.delivering_event
        self._processing_failed = state.processing_failed
        self._symbol = state.symbol
        self._cash = state.cash
        self._quantity = state.quantity
        self._average_cost = state.average_cost
        self._realized_pnl = state.realized_pnl
        self._cumulative_fees = state.cumulative_fees
        self._cumulative_slippage = state.cumulative_slippage
        self._peak_equity = state.peak_equity
        self._previous_close_equity = state.previous_close_equity
        self._start_of_day_equity = state.start_of_day_equity
        self._current_utc_date = state.current_utc_date
        self._pending_signal = state.pending_signal
        self._last_intent_id = state.last_intent_id
        self._halt_state = state.halt_state
        self._history = list(state.history)
        self._equity_curve = list(state.equity_curve)
        self._order_intents = list(state.order_intents)
        self._risk_decisions = list(state.risk_decisions)
        self._fills = list(state.fills)
        self._trades = list(state.trades)

    def _portfolio_snapshot(self, equity: float) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            equity=equity,
            start_of_day_equity=max(self._start_of_day_equity, 1e-12),
            peak_equity=max(self._peak_equity, equity),
            daily_pnl=equity - self._start_of_day_equity,
            trading_enabled=self.config.trading_enabled,
            kill_switch_active=self.config.kill_switch_active,
            cash=self._cash,
            open_positions=1 if self._quantity > 0 else 0,
            halted=self._halt_state.active,
        )

    def _check_and_latch_portfolio_halt(
        self,
        *,
        bar: MarketBar,
        timestamp: datetime,
        equity: float,
        peak_equity: float,
    ) -> None:
        if self._halt_state.active:
            return
        snapshot = PortfolioSnapshot(
            equity=equity,
            start_of_day_equity=max(self._start_of_day_equity, 1e-12),
            peak_equity=max(peak_equity, equity),
            daily_pnl=equity - self._start_of_day_equity,
            trading_enabled=self.config.trading_enabled,
            kill_switch_active=self.config.kill_switch_active,
            cash=self._cash,
            open_positions=1 if self._quantity > 0 else 0,
        )
        try:
            decision = evaluate_portfolio_halt(self.policy, snapshot)
        except Exception:
            decision = RiskDecision(
                status=RiskStatus.REJECTED,
                reasons=(RiskReason.RISK_EVALUATION_ERROR,),
            )
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
        self._latch_halt(timestamp, decision.reasons, bar=bar)

    def _latch_halt(
        self,
        timestamp: datetime,
        reasons: tuple[RiskReason, ...],
        *,
        bar: MarketBar | None = None,
    ) -> None:
        if self._halt_state.active:
            return
        self._halt_state = HaltState(True, timestamp, reasons)
        event_bar = bar or (self._history[-1] if self._history else None)
        if event_bar is not None:
            self._emit(
                "risk_rejected",
                event_bar,
                event_timestamp=timestamp.isoformat(),
                intent_id=None,
                risk_reasons=[reason.value for reason in reasons],
                halt_state="halted",
            )

    def _emit_bar_event(self, bar: MarketBar, signal: Signal, point: EquityPoint) -> None:
        self._emit(
            "portfolio_updated",
            bar,
            signal_timestamp=signal.timestamp.isoformat(),
            target_weight=signal.target_weight,
            cash=point.cash,
            quantity=point.quantity,
            average_cost=point.average_cost,
            equity=point.equity,
            exposure=point.exposure_pct,
            drawdown=point.drawdown,
            halt_state="halted" if point.halt_state.active else "active",
        )

    def _emit(self, event: str, bar: MarketBar, **details: object) -> None:
        if self._event_sink is None:
            return
        payload: dict[str, object] = {
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event": event,
            "session_id": self.session_id,
            "strategy_name": self._strategy_name,
            "symbol": bar.symbol,
            "event_timestamp": bar.timestamp.isoformat(),
        }
        payload.update(details)
        if self._event_buffer is not None:
            self._event_buffer.append(payload)
        else:
            self.publish_event(payload)

    def _money(self, value: float) -> float:
        return round(value, self.config.money_precision)

    def _quantity_value(self, value: float) -> float:
        return round(value, self.config.quantity_precision)


def validate_simulation_bars(
    bars: Sequence[MarketBar],
    *,
    metadata: MarketDataMetadata | None = None,
) -> tuple[MarketBar, ...]:
    """Validate a complete sequence before any batch or replay state mutation."""

    selected = tuple(bars)
    if not selected:
        raise ValueError("bars must not be empty")
    first = selected[0]
    previous = None
    for bar in selected:
        if bar.open is None:
            raise DataValidationError(
                f"bar {bar.timestamp.isoformat()} requires open for next_bar_open execution; "
                "close fallback is prohibited"
            )
        if bar.symbol != first.symbol:
            raise DataValidationError("simulation bars must contain exactly one symbol")
        if bar.timeframe_seconds != first.timeframe_seconds:
            raise DataValidationError("simulation bars must use one consistent timeframe")
        if previous is not None and bar.timestamp <= previous:
            raise DataValidationError(
                "simulation bars must be strictly ascending without duplicates"
            )
        previous = bar.timestamp
    if metadata is not None:
        if metadata.symbol != first.symbol:
            raise DataValidationError("input metadata symbol does not match simulation bars")
        if metadata.row_count != len(selected):
            raise DataValidationError("input metadata row_count does not match simulation bars")
        if metadata.start_timestamp != first.timestamp:
            raise DataValidationError(
                "input metadata start_timestamp does not match simulation bars"
            )
        if metadata.end_timestamp != selected[-1].timestamp:
            raise DataValidationError("input metadata end_timestamp does not match simulation bars")
        if metadata.timeframe_seconds != first.timeframe_seconds:
            raise DataValidationError("input metadata timeframe does not match simulation bars")
        if metadata.bars_sha256 != bars_content_sha256(selected):
            raise DataValidationError(
                "input metadata normalized-bar hash does not match simulation bars"
            )
    return selected


def build_market_data_metadata(
    bars: Sequence[MarketBar],
    *,
    source: str = "in_memory",
) -> MarketDataMetadata:
    """Build content-bound metadata for an already validated in-memory sequence."""

    selected = validate_simulation_bars(bars)
    return MarketDataMetadata(
        source=source,
        content_sha256=bars_content_sha256(selected),
        bars_sha256=bars_content_sha256(selected),
        symbol=selected[0].symbol,
        row_count=len(selected),
        start_timestamp=selected[0].timestamp,
        end_timestamp=selected[-1].timestamp,
        timeframe_seconds=selected[0].timeframe_seconds,
    )


def run_backtest(
    bars: Sequence[MarketBar],
    *,
    strategy: Strategy,
    policy: RiskPolicy | None = None,
    config: BacktestConfig | None = None,
    metadata: MarketDataMetadata | None = None,
    warnings: Sequence[DataWarning] = (),
    event_sink: EventSink | None = None,
) -> BacktestResult:
    """Run a deterministic batch simulation over a validated bar sequence."""

    selected_bars = validate_simulation_bars(bars, metadata=metadata)
    resolved_metadata = metadata or build_market_data_metadata(selected_bars)
    engine = SimulationEngine(
        strategy=strategy,
        policy=policy or RiskPolicy(allowed_symbols=(selected_bars[0].symbol,)),
        config=config or BacktestConfig(),
        metadata=resolved_metadata,
        warnings=warnings,
        validated_bars=selected_bars,
        event_sink=event_sink,
    )
    for bar in selected_bars:
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


def _risk_configuration(policy: RiskPolicy) -> RiskConfiguration:
    return RiskConfiguration(
        allow_live_trading=policy.allow_live_trading,
        allow_shorting=policy.allow_shorting,
        allow_leverage=policy.allow_leverage,
        max_asset_weight=policy.max_asset_weight,
        max_total_gross_exposure=policy.max_total_gross_exposure,
        max_order_notional_weight=policy.max_order_notional_weight,
        max_daily_loss_pct=policy.max_daily_loss_pct,
        max_drawdown_pct=policy.max_drawdown_pct,
        max_data_age_seconds=policy.max_data_age_seconds,
        max_open_positions=policy.max_open_positions,
        allowed_symbols=policy.allowed_symbols,
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
    config: BacktestConfig,
) -> BenchmarkComparison:
    starting_cash = config.initial_cash
    purchase_index = config.warmup_bars
    purchase_bar = bars[purchase_index] if purchase_index < len(bars) else None
    quantity = 0.0
    fee = 0.0
    slippage_cost = 0.0
    residual_cash = starting_cash
    reference_price: float | None = None
    execution_price: float | None = None
    if purchase_bar is not None:
        reference_price = purchase_bar.open
        if reference_price is None:
            raise DataValidationError("buy-and-hold benchmark requires an executable bar open")
        execution_price = _execution_price(
            reference_price,
            OrderSide.BUY,
            config.slippage_bps,
        )
        quantity, residual_cash, fee = _largest_affordable_benchmark_purchase(
            execution_price,
            config,
        )
        slippage_cost = round(
            quantity * abs(execution_price - reference_price),
            config.money_precision,
        )

    equity_values = [starting_cash]
    exposures: list[float] = []
    for index, bar in enumerate(bars):
        active_quantity = quantity if purchase_bar is not None and index >= purchase_index else 0.0
        active_cash = residual_cash if active_quantity > 0 else starting_cash
        position_value = round(active_quantity * bar.close, config.money_precision)
        equity = round(active_cash + position_value, config.money_precision)
        equity_values.append(equity)
        exposures.append(position_value / equity if equity > 0 else 0.0)

    ending_equity = equity_values[-1]
    position_open = quantity > 0
    methodology = (
        "Long-only purchase at the open of the first bar after warmup_bars; adverse buy "
        "slippage, configured fee/minimum fee, and quantity precision apply; residual cash "
        "remains nonnegative; closes mark the open position; no final sale is fabricated."
    )
    buy_and_hold = BenchmarkResult(
        name="buy_and_hold",
        start_timestamp=bars[0].timestamp,
        end_timestamp=bars[-1].timestamp,
        starting_cash=starting_cash,
        ending_equity=ending_equity,
        total_return=(ending_equity / starting_cash) - 1.0,
        max_drawdown=_max_drawdown_from_values(equity_values),
        total_fees_paid=fee,
        estimated_slippage_cost=slippage_cost,
        average_exposure=sum(exposures) / len(exposures),
        max_exposure=max(exposures),
        ending_position_open=position_open,
        quantity=quantity,
        purchase_timestamp=(purchase_bar.timestamp if position_open and purchase_bar else None),
        purchase_reference_price=reference_price if position_open else None,
        purchase_execution_price=execution_price if position_open else None,
        fractional_quantity_supported=config.quantity_precision > 0,
        methodology=methodology,
    )
    cash = BenchmarkResult(
        name="cash",
        start_timestamp=bars[0].timestamp,
        end_timestamp=bars[-1].timestamp,
        starting_cash=starting_cash,
        ending_equity=starting_cash,
        total_return=0.0,
        max_drawdown=0.0,
        total_fees_paid=0.0,
        estimated_slippage_cost=0.0,
        average_exposure=0.0,
        max_exposure=0.0,
        ending_position_open=False,
        quantity=0.0,
        purchase_timestamp=None,
        purchase_reference_price=None,
        purchase_execution_price=None,
        fractional_quantity_supported=config.quantity_precision > 0,
        methodology="Cash remains uninvested for the full comparison interval.",
    )
    return BenchmarkComparison(buy_and_hold=buy_and_hold, cash=cash)


def _largest_affordable_benchmark_purchase(
    execution_price: float,
    config: BacktestConfig,
) -> tuple[float, float, float]:
    """Return precision-safe quantity, residual cash, and fee for one buy."""

    scale = 10**config.quantity_precision
    high = int((config.initial_cash / execution_price) * scale)
    low = 1
    selected = (0.0, config.initial_cash, 0.0)
    while low <= high:
        units = (low + high) // 2
        quantity = units / scale
        notional = quantity * execution_price
        cash_notional = round(notional, config.money_precision)
        fee = round(_trade_fee(notional, config), config.money_precision)
        residual = round(config.initial_cash - cash_notional - fee, config.money_precision)
        if residual >= 0:
            selected = (quantity, residual, fee)
            low = units + 1
        else:
            high = units - 1
    return selected


def _max_drawdown_from_values(values: Sequence[float]) -> float:
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _validated_strategy_configuration(
    strategy: Strategy,
) -> tuple[tuple[str, str | int | float | bool], ...]:
    raw = getattr(strategy, "configuration", ())
    if not isinstance(raw, tuple):
        raise ValueError("strategy configuration must be a tuple of key/value pairs")
    selected: list[tuple[str, str | int | float | bool]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("strategy configuration must contain key/value pairs")
        key, value = item
        normalized_key = key.strip() if isinstance(key, str) else ""
        if not normalized_key or normalized_key in seen:
            raise ValueError("strategy configuration keys must be unique non-empty strings")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("strategy configuration values must be primitive types")
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("strategy configuration float values must be finite")
        seen.add(normalized_key)
        selected.append((normalized_key, value))
    return tuple(selected)


def _validate_builtin_strategy_history(strategy: Strategy, config: BacktestConfig) -> None:
    if (
        isinstance(strategy, MovingAverageStrategy)
        and config.strategy_history_limit < strategy.slow_window
    ):
        raise ValueError(
            "strategy_history_limit must be greater than or equal to the moving-average slow_window"
        )


def _derive_session_id(
    strategy_name: str,
    strategy_configuration: tuple[tuple[str, str | int | float | bool], ...],
    policy: RiskPolicy,
    config: BacktestConfig,
    metadata: MarketDataMetadata | None,
    bars_content_digest: str | None,
) -> str:
    metadata_identity = (
        None
        if metadata is None
        else (
            metadata.content_sha256,
            metadata.bars_sha256,
            metadata.symbol,
            metadata.row_count,
            metadata.start_timestamp.isoformat(),
            metadata.end_timestamp.isoformat(),
            metadata.timeframe_seconds,
            metadata.timezone,
        )
    )
    material = "|".join(
        (
            ENGINE_VERSION,
            strategy_name,
            repr(strategy_configuration),
            repr(policy),
            repr(config),
            repr(metadata_identity),
            repr(bars_content_digest),
        )
    )
    return f"sim-{sha256(material.encode('utf-8')).hexdigest()[:16]}"


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
    "build_market_data_metadata",
    "run_backtest",
    "run_moving_average_backtest",
    "validate_simulation_bars",
]
