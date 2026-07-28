"""Framework-independent domain objects for deterministic simulation.

The active platform is deliberately limited to backtests and local historical
replay.  Domain objects contain no broker, exchange, network, or persistence
side effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from re import fullmatch
from typing import Protocol


class DomainValidationError(ValueError):
    """Raised when a domain object would represent invalid market state."""


class DataValidationError(DomainValidationError):
    """Raised when input market data fails closed validation."""


class SessionStateError(RuntimeError):
    """Raised when a paper-replay lifecycle transition is illegal."""


class ExecutionTiming(StrEnum):
    """Supported signal-to-fill timing models."""

    NEXT_BAR_OPEN = "next_bar_open"


class ExecutionPhase(StrEnum):
    """Phase within the timestamp-labelled execution bar."""

    OPEN = "open"


@dataclass(frozen=True)
class BacktestConfig:
    """Resolved simulation, cost, precision, and safety assumptions."""

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
    strategy_history_limit: int = 10_000

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
        _require_int_at_least(self.max_open_positions, "max_open_positions", minimum=1)
        _require_int_at_least(self.warmup_bars, "warmup_bars", minimum=0)
        _require_int_at_least(self.data_age_seconds, "data_age_seconds", minimum=0)
        _require_int_at_least(self.quantity_precision, "quantity_precision", minimum=0)
        _require_int_at_least(self.money_precision, "money_precision", minimum=8)
        _require_int_at_least(
            self.strategy_history_limit,
            "strategy_history_limit",
            minimum=1,
        )
        if self.quantity_precision > 12:
            raise ValueError("quantity_precision must be between 0 and 12")
        if self.money_precision > 12:
            raise ValueError("money_precision must be between 8 and 12")
        if round(self.initial_cash, self.money_precision) != self.initial_cash:
            raise ValueError("initial_cash must be representable at money_precision")
        if type(self.trading_enabled) is not bool:
            raise ValueError("trading_enabled must be a bool")
        if type(self.kill_switch_active) is not bool:
            raise ValueError("kill_switch_active must be a bool")
        try:
            timing = ExecutionTiming(self.execution_timing)
        except (TypeError, ValueError) as exc:
            raise ValueError("execution_timing must be a supported execution model") from exc
        if timing is not ExecutionTiming.NEXT_BAR_OPEN:
            raise ValueError("only next_bar_open execution is supported")
        object.__setattr__(self, "execution_timing", timing)


@dataclass(frozen=True)
class RiskConfiguration:
    """Effective risk limits embedded in a completed simulation result."""

    allow_live_trading: bool
    allow_shorting: bool
    allow_leverage: bool
    max_asset_weight: float
    max_total_gross_exposure: float
    max_order_notional_weight: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_data_age_seconds: int
    max_open_positions: int
    allowed_symbols: tuple[str, ...]


class OrderSide(StrEnum):
    """Long-only order directions."""

    BUY = "buy"
    SELL = "sell"


class RiskStatus(StrEnum):
    """Outcome of a deterministic risk evaluation."""

    APPROVED = "approved"
    REJECTED = "rejected"


class RiskReason(StrEnum):
    """Stable machine-readable risk rejection and halt reasons."""

    INVALID_PORTFOLIO = "invalid portfolio state"
    INVALID_ORDER = "invalid order intent"
    PORTFOLIO_EQUITY_NON_FINITE = "portfolio equity must be finite"
    PORTFOLIO_EQUITY_NON_POSITIVE = "portfolio equity must be positive"
    START_OF_DAY_EQUITY_INVALID = "start-of-day equity must be positive and finite"
    PEAK_EQUITY_INVALID = "peak equity must be positive and finite"
    DAILY_PNL_NON_FINITE = "daily PnL must be finite"
    ORDER_NOTIONAL_NON_FINITE = "order notional must be finite"
    ORDER_NOTIONAL_NON_POSITIVE = "order notional must be positive"
    TOTAL_GROSS_EXPOSURE_NEGATIVE = "resulting total gross exposure must be non-negative"
    DATA_AGE_NEGATIVE = "market data age must be non-negative"
    TRADING_DISABLED = "trading is disabled"
    KILL_SWITCH = "kill switch is active"
    LIVE_TRADING_DISABLED = "live trading is disabled"
    INVALID_SYMBOL = "symbol is not allowed"
    INVALID_DATA = "market data is invalid"
    STALE_DATA = "market data is stale"
    NON_POSITIVE_PRICE = "execution price must be positive"
    INVALID_QUANTITY = "order quantity must be positive"
    INSUFFICIENT_CASH = "available cash is insufficient"
    SHORTING = "short exposure is not allowed"
    LEVERAGE = "leverage is not allowed"
    MAX_ORDER_NOTIONAL = "order notional exceeds max order weight"
    MAX_POSITION = "asset exposure exceeds max asset weight"
    MAX_TOTAL_EXPOSURE = "total gross exposure exceeds max portfolio exposure"
    MAX_OPEN_POSITIONS = "maximum open positions would be exceeded"
    DUPLICATE_INTENT = "duplicate order intent"
    DAILY_LOSS = "daily loss limit breached"
    MAX_DRAWDOWN = "drawdown limit breached"
    HALTED = "portfolio is halted"
    PROJECTED_EQUITY_NON_POSITIVE = "projected post-fill equity must be positive"
    RISK_EVALUATION_ERROR = "risk evaluation failed closed"


class WarningCode(StrEnum):
    """Stable data and simulation warning categories."""

    LARGE_TIME_GAP = "large_time_gap"
    MISSING_VOLUME = "missing_volume"
    SYNTHETIC_DATA = "synthetic_data"
    HYPOTHETICAL_RESULTS = "hypothetical_results"
    LOCAL_ARTIFACT = "local_artifact"
    EVENT_SINK_FAILURE = "event_sink_failure"


class PaperSessionStatus(StrEnum):
    """Explicit local paper-replay lifecycle states."""

    CREATED = "created"
    VALIDATED = "validated"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    HALTED = "halted"
    FAILED = "failed"


def normalize_timestamp_utc(value: datetime | date) -> datetime:
    """Return a timezone-aware UTC timestamp.

    Calendar dates are interpreted as midnight UTC.  Naive datetimes are
    rejected because silently guessing their timezone can shift a trading bar.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainValidationError("datetime timestamps must include a timezone")
        return value.astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


@dataclass(frozen=True, init=False)
class MarketBar:
    """Canonical immutable OHLCV market-data event.

    `timestamp` marks the bar close in UTC.  `open`, `high`, and `low` remain
    optional at the domain boundary, but the default execution model requires
    `open` whenever a pending order is eligible to fill.

    The `date=` constructor argument and `.date` property preserve compatibility
    with the original daily-bar API while all internal ordering uses UTC.
    """

    timestamp: datetime
    symbol: str
    close: float
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    timeframe_seconds: int

    def __init__(
        self,
        *,
        timestamp: datetime | date | None = None,
        date: date | None = None,
        symbol: str,
        close: float,
        open: float | None = None,
        high: float | None = None,
        low: float | None = None,
        volume: float | None = None,
        timeframe_seconds: int = 86_400,
    ) -> None:
        if timestamp is not None and date is not None:
            raise DomainValidationError("provide timestamp or date, not both")
        raw_timestamp = timestamp if timestamp is not None else date
        if raw_timestamp is None:
            raise DomainValidationError("timestamp is required")

        normalized_symbol = normalize_symbol(symbol, field_name="symbol")
        if type(timeframe_seconds) is not int or timeframe_seconds <= 0:
            raise DomainValidationError("timeframe_seconds must be a positive integer")

        values = {"close": close, "open": open, "high": high, "low": low}
        for name, value in values.items():
            if value is not None and (not _is_finite_number(value) or value <= 0):
                raise DomainValidationError(f"{name} must be positive and finite")
        if volume is not None and (not _is_finite_number(volume) or volume < 0):
            raise DomainValidationError("volume must be non-negative and finite")
        if high is not None and low is not None and high < low:
            raise DomainValidationError("high must be greater than or equal to low")
        upper_members = [value for value in (open, close) if value is not None]
        lower_members = [value for value in (open, close) if value is not None]
        if high is not None and upper_members and high < max(upper_members):
            raise DomainValidationError("high must be at least open and close")
        if low is not None and lower_members and low > min(lower_members):
            raise DomainValidationError("low must be at most open and close")

        object.__setattr__(self, "timestamp", normalize_timestamp_utc(raw_timestamp))
        object.__setattr__(self, "symbol", normalized_symbol)
        object.__setattr__(self, "close", float(close))
        object.__setattr__(self, "open", None if open is None else float(open))
        object.__setattr__(self, "high", None if high is None else float(high))
        object.__setattr__(self, "low", None if low is None else float(low))
        object.__setattr__(self, "volume", None if volume is None else float(volume))
        object.__setattr__(self, "timeframe_seconds", timeframe_seconds)

    @property
    def date(self) -> date:
        """Return the UTC calendar date for legacy daily-bar callers."""

        return self.timestamp.date()


PriceBar = MarketBar


@dataclass(frozen=True)
class DataWarning:
    """Actionable non-fatal input-data or simulation warning."""

    code: WarningCode
    message: str
    timestamp: datetime | None = None

    @property
    def is_data_validation(self) -> bool:
        """Return whether the warning describes the input dataset itself."""

        return self.code in {
            WarningCode.LARGE_TIME_GAP,
            WarningCode.MISSING_VOLUME,
            WarningCode.SYNTHETIC_DATA,
        }


@dataclass(frozen=True)
class MarketDataMetadata:
    """Stable metadata describing validated input data."""

    source: str
    content_sha256: str
    bars_sha256: str
    symbol: str
    row_count: int
    start_timestamp: datetime
    end_timestamp: datetime
    timeframe_seconds: int
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if not isinstance(self.source, str):
            raise DomainValidationError("market-data source must be a string")
        normalized_source = self.source.strip()
        if not normalized_source:
            raise DomainValidationError("market-data source must be non-empty")
        if "/" in normalized_source or "\\" in normalized_source or ":" in normalized_source:
            raise DomainValidationError("market-data source must be a filename, not a path")
        if normalized_source in {".", ".."}:
            raise DomainValidationError("market-data source must be a safe filename")
        if (
            not isinstance(self.content_sha256, str)
            or fullmatch(r"[0-9a-f]{64}", self.content_sha256) is None
        ):
            raise DomainValidationError("market-data content_sha256 must be lowercase SHA-256")
        if (
            not isinstance(self.bars_sha256, str)
            or fullmatch(r"[0-9a-f]{64}", self.bars_sha256) is None
        ):
            raise DomainValidationError("market-data bars_sha256 must be lowercase SHA-256")
        normalized_symbol = normalize_symbol(
            self.symbol,
            field_name="market-data symbol",
        )
        if type(self.row_count) is not int or self.row_count <= 0:
            raise DomainValidationError("market-data row_count must be a positive integer")
        if type(self.timeframe_seconds) is not int or self.timeframe_seconds <= 0:
            raise DomainValidationError("market-data timeframe_seconds must be a positive integer")
        start = normalize_timestamp_utc(self.start_timestamp)
        end = normalize_timestamp_utc(self.end_timestamp)
        if end < start:
            raise DomainValidationError("market-data end_timestamp cannot precede start_timestamp")
        if self.timezone != "UTC":
            raise DomainValidationError("market-data timezone must be UTC")
        object.__setattr__(self, "source", normalized_source)
        object.__setattr__(self, "symbol", normalized_symbol)
        object.__setattr__(self, "start_timestamp", start)
        object.__setattr__(self, "end_timestamp", end)


@dataclass(frozen=True)
class MarketDataSet:
    """Validated market bars plus metadata and non-fatal warnings."""

    bars: tuple[MarketBar, ...]
    metadata: MarketDataMetadata
    warnings: tuple[DataWarning, ...] = ()

    def __post_init__(self) -> None:
        if not self.bars:
            raise DomainValidationError("market-data bars must not be empty")
        if len(self.bars) != self.metadata.row_count:
            raise DomainValidationError("market-data metadata row_count does not match bars")
        if self.bars[0].timestamp != self.metadata.start_timestamp:
            raise DomainValidationError("market-data metadata start_timestamp does not match bars")
        if self.bars[-1].timestamp != self.metadata.end_timestamp:
            raise DomainValidationError("market-data metadata end_timestamp does not match bars")
        for bar in self.bars:
            if bar.symbol != self.metadata.symbol:
                raise DomainValidationError("market-data metadata symbol does not match bars")
            if bar.timeframe_seconds != self.metadata.timeframe_seconds:
                raise DomainValidationError("market-data metadata timeframe does not match bars")


@dataclass(frozen=True)
class Signal:
    """Strategy target generated only from bars available at `timestamp`."""

    timestamp: datetime
    symbol: str
    target_weight: float
    strategy_name: str

    def __post_init__(self) -> None:
        if not _is_finite_number(self.target_weight) or not 0 <= self.target_weight <= 1:
            raise DomainValidationError("target_weight must be finite and between 0 and 1")
        if not isinstance(self.strategy_name, str):
            raise DomainValidationError("signal strategy_name must be a string")
        normalized_symbol = normalize_symbol(self.symbol, field_name="signal symbol")
        normalized_strategy = self.strategy_name.strip()
        if not normalized_strategy:
            raise DomainValidationError("signal strategy_name must be non-empty")
        object.__setattr__(self, "timestamp", normalize_timestamp_utc(self.timestamp))
        object.__setattr__(self, "symbol", normalized_symbol)
        object.__setattr__(self, "strategy_name", normalized_strategy)


class Strategy(Protocol):
    """Read-only strategy interface; strategies cannot mutate portfolio state."""

    @property
    def name(self) -> str:
        """Return a stable strategy name."""

    @property
    def configuration(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        """Return stable primitive parameters used for reproducibility."""

    def signal_for_history(self, history: Sequence[MarketBar]) -> Signal:
        """Return a target using only the supplied historical prefix."""


@dataclass(frozen=True)
class Position:
    """Immutable long-only position snapshot."""

    symbol: str
    quantity: float = 0.0
    average_cost: float = 0.0


@dataclass(frozen=True)
class PortfolioState:
    """Immutable portfolio accounting snapshot."""

    cash: float
    position: Position
    realized_pnl: float
    cumulative_fees: float
    cumulative_slippage: float
    equity: float
    peak_equity: float
    start_of_day_equity: float


@dataclass(frozen=True)
class BacktestState:
    """Incremental simulation state suitable for backtest or replay adapters."""

    timestamp: datetime | None
    bars_processed: int
    portfolio: PortfolioState
    pending_signal: Signal | None
    halt_state: HaltState


@dataclass(frozen=True)
class OrderIntent:
    """Strategy target translated into a proposed, not-yet-approved order."""

    intent_id: str
    signal_timestamp: datetime
    execution_timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: float
    reference_price: float
    estimated_execution_price: float
    estimated_fee: float
    target_weight: float
    execution_phase: ExecutionPhase = ExecutionPhase.OPEN

    @property
    def notional(self) -> float:
        """Return estimated executed notional."""

        return abs(self.quantity * self.estimated_execution_price)


@dataclass(frozen=True)
class Fill:
    """Approved simulated execution."""

    intent_id: str
    fill_id: str
    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: float
    reference_price: float
    execution_price: float
    fee: float
    slippage_cost: float
    execution_phase: ExecutionPhase = ExecutionPhase.OPEN


@dataclass(frozen=True)
class Trade:
    """Completed simulated position change with accounting impact."""

    fill: Fill
    signal_timestamp: datetime
    average_cost_after: float
    realized_pnl_delta: float
    resulting_cash: float
    resulting_quantity: float
    target_weight: float

    @property
    def date(self) -> str:
        return self.fill.timestamp.date().isoformat()

    @property
    def signal_date(self) -> str:
        """Signal date is encoded in the deterministic intent identifier."""

        return self.fill.intent_id.split(":", maxsplit=1)[0]

    @property
    def symbol(self) -> str:
        return self.fill.symbol

    @property
    def side(self) -> str:
        return self.fill.side.value

    @property
    def shares(self) -> float:
        return self.fill.quantity

    @property
    def reference_price(self) -> float:
        return self.fill.reference_price

    @property
    def execution_price(self) -> float:
        return self.fill.execution_price

    @property
    def notional(self) -> float:
        return self.fill.quantity * self.fill.execution_price

    @property
    def fee_paid(self) -> float:
        return self.fill.fee

    @property
    def slippage_cost(self) -> float:
        return self.fill.slippage_cost


@dataclass(frozen=True)
class HaltState:
    """Latched circuit-breaker state."""

    active: bool = False
    timestamp: datetime | None = None
    reasons: tuple[RiskReason, ...] = ()


@dataclass(frozen=True)
class RiskDecisionRecord:
    """Auditable risk decision tied to an order intent or portfolio check."""

    timestamp: datetime
    status: RiskStatus
    reasons: tuple[RiskReason, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    intent_id: str | None = None


@dataclass(frozen=True)
class EquityPoint:
    """End-of-bar portfolio and accounting state."""

    timestamp: datetime
    close: float
    cash: float
    quantity: float
    average_cost: float
    position_market_value: float
    equity: float
    start_of_day_equity: float
    daily_pnl: float
    peak_equity: float
    exposure_pct: float
    realized_pnl: float
    unrealized_pnl: float
    cumulative_fees: float
    cumulative_slippage: float
    drawdown: float
    halt_state: HaltState
    target_weight_for_next_bar: float

    @property
    def date(self) -> str:
        """Return the UTC date string retained by the legacy report API."""

        return self.timestamp.date().isoformat()

    @property
    def shares(self) -> float:
        """Legacy alias for quantity."""

        return self.quantity

    @property
    def exposure(self) -> float:
        """Legacy alias for position market value."""

        return self.position_market_value


@dataclass(frozen=True)
class BacktestSummary:
    """Stable strategy result summary."""

    start_timestamp: datetime
    end_timestamp: datetime
    starting_cash: float
    ending_equity: float
    total_return: float
    max_drawdown: float
    number_of_trades: int
    turnover: float
    total_fees_paid: float
    estimated_slippage_cost: float
    average_exposure: float
    max_exposure: float
    realized_pnl: float
    unrealized_pnl: float
    risk_halt_triggered: bool
    rejected_order_count: int
    warning_count: int

    @property
    def start_date(self) -> str:
        """Legacy UTC date alias."""

        return self.start_timestamp.date().isoformat()

    @property
    def end_date(self) -> str:
        """Legacy UTC date alias."""

        return self.end_timestamp.date().isoformat()


@dataclass(frozen=True)
class BenchmarkResult:
    """Non-strategy comparison baseline."""

    name: str
    start_timestamp: datetime
    end_timestamp: datetime
    starting_cash: float
    ending_equity: float
    total_return: float
    max_drawdown: float
    total_fees_paid: float
    estimated_slippage_cost: float
    average_exposure: float
    max_exposure: float
    ending_position_open: bool
    quantity: float
    purchase_timestamp: datetime | None
    purchase_reference_price: float | None
    purchase_execution_price: float | None
    fractional_quantity_supported: bool
    methodology: str

    @property
    def start_date(self) -> str:
        return self.start_timestamp.date().isoformat()

    @property
    def end_date(self) -> str:
        return self.end_timestamp.date().isoformat()


@dataclass(frozen=True)
class BenchmarkComparison:
    """Buy-and-hold and cash baselines over the same input bars."""

    buy_and_hold: BenchmarkResult
    cash: BenchmarkResult


@dataclass(frozen=True)
class BacktestResult:
    """Complete deterministic simulation output."""

    session_id: str
    strategy_name: str
    strategy_configuration: tuple[tuple[str, str | int | float | bool], ...]
    symbol: str
    input_metadata: MarketDataMetadata
    assumptions: BacktestConfig
    risk_configuration: RiskConfiguration
    summary: BacktestSummary
    benchmarks: BenchmarkComparison
    equity_curve: tuple[EquityPoint, ...]
    order_intents: tuple[OrderIntent, ...]
    risk_decisions: tuple[RiskDecisionRecord, ...]
    fills: tuple[Fill, ...]
    trades: tuple[Trade, ...]
    halt_state: HaltState
    warnings: tuple[DataWarning, ...]

    @property
    def initial_equity(self) -> float:
        return self.summary.starting_cash

    @property
    def final_equity(self) -> float:
        return self.summary.ending_equity

    @property
    def total_return(self) -> float:
        return self.summary.total_return

    @property
    def max_drawdown(self) -> float:
        return self.summary.max_drawdown

    @property
    def risk_rejections(self) -> tuple[RiskDecisionRecord, ...]:
        """Compatibility view of rejected risk decisions."""

        return tuple(
            decision
            for decision in self.risk_decisions
            if decision.status is RiskStatus.REJECTED and decision.intent_id is not None
        )


@dataclass(frozen=True)
class ReportOutput:
    """Description of a generated local report artifact."""

    path: str
    schema_version: str
    media_type: str


@dataclass(frozen=True)
class ModelForecast:
    """Disabled future-ML boundary; the active engine does not consume it."""

    timestamp: datetime
    symbol: str
    model_version: str
    expected_return_score: float | None = None
    probability: float | None = None
    confidence: float | None = None
    volatility_estimate: float | None = None
    regime_label: str | None = None
    target_allocation_suggestion: float | None = None


@dataclass(frozen=True)
class SessionTransition:
    """Paper-replay lifecycle transition."""

    timestamp: datetime
    from_status: PaperSessionStatus
    to_status: PaperSessionStatus
    reason: str


@dataclass(frozen=True)
class PaperSessionSummary:
    """Reproducible local paper-replay result."""

    session_id: str
    status: PaperSessionStatus
    bars_processed: int
    total_bars: int
    replay_speed_seconds: float
    random_seed: int
    strategy_name: str
    strategy_configuration: tuple[tuple[str, str | int | float | bool], ...]
    engine_version: str
    input_metadata: MarketDataMetadata
    assumptions: BacktestConfig
    risk_configuration: RiskConfiguration
    start_event_timestamp: datetime | None
    end_event_timestamp: datetime | None
    halt_reasons: tuple[RiskReason, ...]
    failure_reason: str | None
    warnings: tuple[DataWarning, ...]
    transitions: tuple[SessionTransition, ...]
    result: BacktestResult | None


def normalize_symbol(value: object, *, field_name: str = "symbol") -> str:
    """Return an uppercase symbol safe for reports and spreadsheet exports."""

    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a string")
    normalized = value.strip().upper()
    if fullmatch(r"[A-Z0-9][A-Z0-9._:-]{0,31}", normalized) is None:
        raise DomainValidationError(
            f"{field_name} must use 1-32 letters, digits, dot, underscore, colon, or hyphen"
        )
    return normalized


def _require_positive(value: float, name: str) -> None:
    if not _is_finite_number(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def _require_non_negative(value: float, name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")


def _require_fraction(value: float, name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and between 0 and 1")


def _require_int_at_least(value: int, name: str, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
