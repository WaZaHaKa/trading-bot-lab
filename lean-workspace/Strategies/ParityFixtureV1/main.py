from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

from AlgorithmImports import (
    AccountType,
    BrokerageName,
    CashAmount,
    FeeModel,
    FileFormat,
    Globals,
    ImmediateFillModel,
    OrderFee,
    OrderStatus,
    PythonData,
    QCAlgorithm,
    Resolution,
    Slice,
    SubscriptionDataSource,
    SubscriptionTransportMedium,
    TimeZones,
)


OBSERVATION_PREFIX = "TRADING_BOT_LAB_LEAN_PARITY_V1:"
MAX_OBSERVATION_BYTES = 64_000
CONTRACT_NAME = "trading_bot_lab_cross_engine_parity"
CONTRACT_VERSION = "1.0.0"
TRACE_SCHEMA_VERSION = "1.0.0"
SCENARIO_MANIFEST_VERSION = "1.0.0"
SCENARIO_ID = "weekday_ma_next_open_v1"
FIXTURE_NAME = "synthetic_weekdays.csv"
FIXTURE_SHA256 = "a68bcf7fc30d2593b32e5a98852c4f8e0190ed99865640485b344515d9f1f78a"
NORMALIZED_BARS_SHA256 = "02394c31af7b982493bcbdadd92735d7a0ee6ae04e3d38b7e3e3a5fde6cbce6d"
CONTRACT_SHA256 = "81d300e5567ab7f6c3f6b49058542ac229f4597efa248036fe5d2879fc1efe8d"
SCENARIO_MANIFEST_SHA256 = "8179874de11817c421f7ddcfdb2f1f173308009953a576a954fd8e3f6b58bf4a"
SCENARIO_SCHEMA_SHA256 = "6ff72c73631abf5ef7bd32a39cb6ff62116c7d4094b8b0e296c5efa981b650bc"
TRACE_SCHEMA_SHA256 = "8b9b439f848abef8b7e508640498bd392e0dfd06206fdc96a6d5fea6b38a22f8"

SYMBOL = "PARITY"
TIMEFRAME_SECONDS = 86_400
EXPECTED_ROW_COUNT = 8
FIXTURE_HEADER = "date,symbol,open,high,low,close,volume"
LOCAL_TRANSPORT = "local-file"
OBJECT_STORE_TRANSPORT = "object-store"
LOCAL_FIXTURE_PARTS = ("custom", "parity", "v1", FIXTURE_NAME)
OBJECT_STORE_KEY = "trading-bot-lab/parity/v1/synthetic_weekdays.csv"

INITIAL_CASH = Decimal("100000")
FAST_WINDOW = 2
SLOW_WINDOW = 3
TARGET_WEIGHT = Decimal("0.1")
FEE_BPS = Decimal("1")
SLIPPAGE_BPS = Decimal("2")
MAX_POSITION_WEIGHT = Decimal("0.1")
MAX_TOTAL_EXPOSURE = Decimal("0.3")
MAX_ORDER_NOTIONAL_WEIGHT = Decimal("0.1")
MAX_DAILY_LOSS = Decimal("0.02")
MAX_DRAWDOWN = Decimal("0.05")
MONEY_PRECISION = 8
QUANTITY_PRECISION = 0
STRATEGY_HISTORY_LIMIT = 100
MONEY_QUANTUM = Decimal("0.00000001")
LEAN_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")


class FixtureBar(NamedTuple):
    session: date
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_line: str

    @property
    def timestamp(self) -> str:
        return f"{self.session.isoformat()}T00:00:00+00:00"


class TransportSelection(NamedTuple):
    name: str
    source: str
    medium: object


class PendingSignal(NamedTuple):
    timestamp: str
    target_weight: Decimal


def canonical_decimal(value: object) -> str:
    """Return a finite, exponent-free decimal string for the v1 trace."""

    if isinstance(value, bool):
        raise ValueError("boolean values are not parity decimals")
    try:
        selected = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("parity numeric value must be a decimal") from exc
    if not selected.is_finite():
        raise ValueError("parity numeric value must be finite")
    if selected == 0:
        return "0"
    rendered = format(selected.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _parse_positive_decimal(raw: str, *, field: str, row_number: int) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"row {row_number} {field} must be a decimal") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"row {row_number} {field} must be positive and finite")
    return value


def parse_fixture_row(line: str, *, row_number: int) -> FixtureBar:
    """Parse one exact v1 CSV row without sorting or normalization."""

    if not isinstance(line, str) or not line:
        raise ValueError(f"row {row_number} must be a non-empty CSV line")
    try:
        fields = next(csv.reader([line], strict=True))
    except (csv.Error, StopIteration) as exc:
        raise ValueError(f"row {row_number} is malformed CSV") from exc
    if len(fields) != 7:
        raise ValueError(f"row {row_number} must contain exactly seven columns")
    raw_date, symbol, raw_open, raw_high, raw_low, raw_close, raw_volume = fields
    try:
        session = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"row {row_number} date must use YYYY-MM-DD") from exc
    if session.isoformat() != raw_date:
        raise ValueError(f"row {row_number} date must use canonical YYYY-MM-DD")
    if session.weekday() >= 5:
        raise ValueError(f"row {row_number} must be a weekday session")
    if symbol != SYMBOL:
        raise ValueError(f"row {row_number} symbol must be {SYMBOL}")

    open_price = _parse_positive_decimal(raw_open, field="open", row_number=row_number)
    high_price = _parse_positive_decimal(raw_high, field="high", row_number=row_number)
    low_price = _parse_positive_decimal(raw_low, field="low", row_number=row_number)
    close_price = _parse_positive_decimal(raw_close, field="close", row_number=row_number)
    if high_price < max(open_price, close_price):
        raise ValueError(f"row {row_number} high is below open or close")
    if low_price > min(open_price, close_price):
        raise ValueError(f"row {row_number} low is above open or close")
    if low_price > high_price:
        raise ValueError(f"row {row_number} low exceeds high")
    if not raw_volume.isascii() or not raw_volume.isdigit():
        raise ValueError(f"row {row_number} volume must be a non-negative integer")
    volume = Decimal(raw_volume)
    return FixtureBar(
        session,
        symbol,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        line,
    )


def parse_fixture_bytes(
    payload: bytes,
    *,
    expected_sha256: str = FIXTURE_SHA256,
) -> tuple[FixtureBar, ...]:
    """Validate the complete fixture before LEAN creates a subscription."""

    if not isinstance(payload, bytes):
        raise ValueError("parity fixture payload must be bytes")
    actual_sha256 = sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("parity fixture SHA-256 does not match the v1 contract")
    if b"\r" in payload:
        raise ValueError("parity fixture must use LF line endings only")
    if not payload.endswith(b"\n"):
        raise ValueError("parity fixture must end with one LF byte")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("parity fixture must be valid UTF-8") from exc
    lines = text.split("\n")
    if lines[-1] != "":
        raise ValueError("parity fixture must end with one LF byte")
    lines.pop()
    if not lines or lines[0] != FIXTURE_HEADER:
        raise ValueError("parity fixture header differs from the v1 contract")
    if len(lines) != EXPECTED_ROW_COUNT + 1:
        raise ValueError("parity fixture must contain exactly eight data rows")

    bars = tuple(
        parse_fixture_row(line, row_number=index) for index, line in enumerate(lines[1:], start=2)
    )
    previous: date | None = None
    for bar in bars:
        if previous is not None and bar.session == previous:
            raise ValueError("parity fixture contains a duplicated timestamp")
        if previous is not None and bar.session < previous:
            raise ValueError("parity fixture timestamps must be sorted ascending")
        previous = bar.session
    if bars[0].session != date(2024, 1, 2) or bars[-1].session != date(2024, 1, 11):
        raise ValueError("parity fixture interval differs from the v1 contract")
    return bars


def local_fixture_source(data_folder: str) -> str:
    """Join the fixed relative fixture path for POSIX or Windows data roots."""

    raw = str(data_folder)
    if not raw or "\x00" in raw:
        raise ValueError("Globals.data_folder must be a non-empty safe path")
    windows = PureWindowsPath(raw)
    if windows.drive or "\\" in raw:
        return str(windows.joinpath(*LOCAL_FIXTURE_PARTS))
    return str(PurePosixPath(raw).joinpath(*LOCAL_FIXTURE_PARTS))


def resolve_transport(
    raw: str,
    object_store_key: str,
    data_folder: str,
) -> TransportSelection:
    """Resolve the only two allowed sources without a remote fallback."""

    if raw == LOCAL_TRANSPORT:
        if object_store_key != "":
            raise ValueError("object-store-key must remain empty for local-file transport")
        return TransportSelection(
            LOCAL_TRANSPORT,
            local_fixture_source(data_folder),
            SubscriptionTransportMedium.LOCAL_FILE,
        )
    if raw == OBJECT_STORE_TRANSPORT:
        if object_store_key != OBJECT_STORE_KEY:
            raise ValueError("object-store-key must equal the fixed public parity v1 key")
        return TransportSelection(
            OBJECT_STORE_TRANSPORT,
            OBJECT_STORE_KEY,
            SubscriptionTransportMedium.OBJECT_STORE,
        )
    raise ValueError("data-transport must be exactly local-file or object-store")


def read_fixture_source(selection: TransportSelection, object_store: object) -> bytes:
    """Read one selected source. There is deliberately no fallback or write path."""

    if selection.name == LOCAL_TRANSPORT:
        selected = Path(selection.source)
        if selected.is_symlink() or not selected.is_file():
            raise ValueError("the staged local parity fixture is missing or unsafe")
        return selected.read_bytes()
    if selection.name == OBJECT_STORE_TRANSPORT:
        if object_store is None or not object_store.contains_key(OBJECT_STORE_KEY):
            raise ValueError("the fixed parity Object Store key is missing")
        raw = object_store.read_bytes(OBJECT_STORE_KEY)
        try:
            return bytes(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("the fixed parity Object Store value is not bytes") from exc
    raise ValueError("unsupported parity transport selection")


class TrailingSignalState:
    """Bounded completed-close signal state with an explicit next-row boundary."""

    def __init__(self) -> None:
        self._closes: list[Decimal] = []
        self._pending: PendingSignal | None = None
        self._last_timestamp: str | None = None

    @property
    def pending(self) -> PendingSignal | None:
        return self._pending

    def begin_bar(self, timestamp: str) -> PendingSignal | None:
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            raise ValueError("fixture bars must advance before pending execution")
        pending = self._pending
        self._pending = None
        return pending

    def finish_bar(self, timestamp: str, close: Decimal) -> Decimal:
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            raise ValueError("fixture bars must be strictly increasing")
        self._last_timestamp = timestamp
        self._closes.append(close)
        if len(self._closes) > STRATEGY_HISTORY_LIMIT:
            self._closes.pop(0)
        if len(self._closes) < SLOW_WINDOW:
            return Decimal("0")
        fast = sum(self._closes[-FAST_WINDOW:], Decimal("0")) / FAST_WINDOW
        slow = sum(self._closes[-SLOW_WINDOW:], Decimal("0")) / SLOW_WINDOW
        target = TARGET_WEIGHT if fast > slow else Decimal("0")
        self._pending = PendingSignal(timestamp, target)
        return target


def parity_money(value: Decimal) -> Decimal:
    """Apply the local engine's fixed eight-place money precision."""

    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def cost_aware_target_quantity(
    *,
    target_weight: Decimal,
    reference_price: Decimal,
    current_quantity: Decimal,
    cash: Decimal,
) -> Decimal:
    """Mirror the local engine's integer target sizing without LEAN buffers."""

    values = (target_weight, reference_price, current_quantity, cash)
    if any(not value.is_finite() for value in values):
        raise ValueError("parity sizing inputs must be finite")
    if reference_price <= 0 or current_quantity < 0 or cash < 0:
        raise ValueError("parity sizing inputs must be nonnegative with a positive price")
    if target_weight < 0 or target_weight > MAX_POSITION_WEIGHT:
        raise ValueError("parity target weight is outside the long-only contract")
    current_units = current_quantity.to_integral_value(rounding=ROUND_DOWN)
    if current_units != current_quantity:
        raise ValueError("parity holdings must use whole shares")
    if target_weight == 0:
        return Decimal("0")

    equity = parity_money(cash + current_quantity * reference_price)
    if equity <= 0:
        return Decimal("0")
    raw_target_units = (target_weight * equity / reference_price).to_integral_value(
        rounding=ROUND_DOWN
    )
    current_market_value = parity_money(current_quantity * reference_price)
    current_weight = current_market_value / equity
    if current_weight <= target_weight:
        low = int(current_units)
        high = max(low, int(raw_target_units))
    else:
        low = 0
        high = int(current_units)

    best_units: int | None = None
    while low <= high:
        candidate_units = (low + high) // 2
        candidate_quantity = Decimal(candidate_units)
        delta = abs(candidate_quantity - current_quantity)
        candidate_market_value = parity_money(candidate_quantity * reference_price)
        execution_notional = Decimal("0")
        is_buy = candidate_quantity > current_quantity
        if delta == 0:
            projected_cash = cash
        else:
            direction = Decimal("1") if is_buy else Decimal("-1")
            execution_price = reference_price * (
                Decimal("1") + direction * SLIPPAGE_BPS / Decimal("10000")
            )
            execution_notional = delta * execution_price
            cash_notional = parity_money(execution_notional)
            fee = parity_money(execution_notional * FEE_BPS / Decimal("10000"))
            projected_cash = parity_money(
                cash - cash_notional - fee if is_buy else cash + cash_notional - fee
            )
        projected_equity = parity_money(projected_cash + candidate_market_value)
        order_within_limit = not is_buy or execution_notional / equity <= MAX_ORDER_NOTIONAL_WEIGHT
        candidate_is_safe = (
            projected_equity > 0
            and candidate_market_value / projected_equity <= target_weight
            and order_within_limit
            and (not is_buy or (candidate_market_value > 0 and execution_notional > 0))
        )
        if candidate_is_safe:
            best_units = candidate_units
            low = candidate_units + 1
        else:
            high = candidate_units - 1
    return Decimal("0") if best_units is None else Decimal(best_units)


class ParityFixtureData(PythonData):
    """One strict custom-data parser shared by local and Object Store sources."""

    _selection: TransportSelection | None = None
    _expected_lines: dict[str, str] = {}

    @classmethod
    def configure(
        cls,
        selection: TransportSelection,
        bars: tuple[FixtureBar, ...],
    ) -> None:
        cls._selection = selection
        cls._expected_lines = {bar.session.isoformat(): bar.source_line for bar in bars}

    def default_resolution(self):
        return Resolution.DAILY

    def is_sparse_data(self) -> bool:
        return True

    def requires_mapping(self) -> bool:
        return False

    def get_source(self, config, selected_date, is_live_mode):
        del config, selected_date
        if is_live_mode:
            raise RuntimeError("ParityFixtureV1 custom data is backtest-only")
        if self._selection is None:
            raise RuntimeError("parity fixture transport was not prevalidated")
        return SubscriptionDataSource(
            self._selection.source,
            self._selection.medium,
            FileFormat.CSV,
        )

    def reader(self, config, line, selected_date, is_live_mode):
        del selected_date
        if is_live_mode:
            raise RuntimeError("ParityFixtureV1 custom data is backtest-only")
        if line == FIXTURE_HEADER:
            return None
        parsed = parse_fixture_row(line, row_number=0)
        if self._expected_lines.get(parsed.session.isoformat()) != line:
            raise ValueError("runtime parity row differs from the prevalidated fixture")
        point = ParityFixtureData()
        point.symbol = config.symbol
        point.time = datetime(parsed.session.year, parsed.session.month, parsed.session.day)
        point.end_time = point.time
        point.value = float(parsed.close)
        point.open = float(parsed.open)
        point.high = float(parsed.high)
        point.low = float(parsed.low)
        point.close = float(parsed.close)
        point.volume = float(parsed.volume)
        point.session_timestamp = parsed.timestamp
        return point


class NotionalBpsFeeModel(FeeModel):
    """LEAN fee hook for the scenario's one-basis-point fee contract."""

    def get_order_fee(self, parameters) -> OrderFee:
        price = Decimal(str(parameters.security.price))
        quantity = abs(Decimal(str(parameters.order.quantity)))
        fee = price * quantity * FEE_BPS / Decimal("10000")
        currency = parameters.security.quote_currency.symbol
        return OrderFee(CashAmount(float(fee), currency))


class NextRowOpenFillModel(ImmediateFillModel):
    """Fill only the active pending order at the current fixture row's open."""

    def __init__(self) -> None:
        super().__init__()
        self._reference_open: Decimal | None = None

    def set_reference_open(self, reference_open: Decimal) -> None:
        if not reference_open.is_finite() or reference_open <= 0:
            raise ValueError("next-row reference open must be positive and finite")
        self._reference_open = reference_open

    def market_fill(self, asset, order):
        if self._reference_open is None:
            raise RuntimeError("next-row reference open was not configured")
        event = super().market_fill(asset, order)
        if event.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            slip = self._reference_open * SLIPPAGE_BPS / Decimal("10000")
            direction = Decimal("1") if Decimal(str(order.quantity)) > 0 else Decimal("-1")
            event.fill_price = float(self._reference_open + direction * slip)
        return event


def canonical_trace_line(trace: dict[str, object]) -> str:
    """Serialize exactly one bounded machine-readable observation line."""

    encoded = json.dumps(
        trace,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    line = OBSERVATION_PREFIX + encoded
    if "\n" in line or "\r" in line:
        raise ValueError("parity observation must occupy exactly one line")
    if len(line.encode("utf-8")) > MAX_OBSERVATION_BYTES:
        raise ValueError("parity observation exceeds its fixed byte bound")
    return line


def _assumptions() -> dict[str, object]:
    return {
        "backtest": {
            "data_age_seconds": 0,
            "execution_timing": "next_bar_open",
            "fee_bps": "1",
            "fee_model": "notional_bps",
            "initial_cash": "100000",
            "kill_switch_active": False,
            "max_daily_loss_pct": "0.02",
            "max_drawdown_pct": "0.05",
            "max_open_positions": 1,
            "max_order_notional_pct": "0.1",
            "max_position_pct": "0.1",
            "max_total_exposure_pct": "0.3",
            "minimum_fee": "0",
            "money_precision": MONEY_PRECISION,
            "quantity_precision": QUANTITY_PRECISION,
            "slippage_bps": "2",
            "slippage_model": "adverse_bps",
            "strategy_history_limit": STRATEGY_HISTORY_LIMIT,
            "trading_enabled": True,
            "warmup_bars": 0,
        },
        "risk": {
            "allow_leverage": False,
            "allow_live_trading": False,
            "allow_shorting": False,
            "allowed_symbols": [SYMBOL],
            "max_asset_weight": "0.1",
            "max_daily_loss_pct": "0.02",
            "max_data_age_seconds": 300,
            "max_drawdown_pct": "0.05",
            "max_open_positions": 1,
            "max_order_notional_weight": "0.1",
            "max_total_gross_exposure": "0.3",
        },
    }


class ParityFixtureV1(QCAlgorithm):
    """Backtest-only LEAN observation over the exact synthetic v1 fixture."""

    def initialize(self) -> None:
        if self.live_mode:
            raise RuntimeError("ParityFixtureV1 is backtest-only; live mode is forbidden")
        transport = self.get_parameter("data-transport", LOCAL_TRANSPORT)
        object_store_key = self.get_parameter("object-store-key", "")
        selection = resolve_transport(transport, object_store_key, Globals.data_folder)
        payload = read_fixture_source(selection, self.object_store)
        self._fixture_bars = parse_fixture_bytes(payload)
        ParityFixtureData.configure(selection, self._fixture_bars)

        self.set_start_date(2024, 1, 2)
        self.set_end_date(2024, 1, 11)
        self.set_time_zone(TimeZones.UTC)
        self.set_cash(float(INITIAL_CASH))
        self.set_brokerage_model(
            BrokerageName.QUANT_CONNECT_BROKERAGE,
            AccountType.CASH,
        )
        security = self.add_data(ParityFixtureData, SYMBOL, Resolution.DAILY)
        security.set_leverage(1.0)
        security.set_fee_model(NotionalBpsFeeModel())
        self._fill_model = NextRowOpenFillModel()
        security.set_fill_model(self._fill_model)
        self._security = security
        self._symbol = security.symbol
        self.set_benchmark(self._symbol)

        self._signal_state = TrailingSignalState()
        self._bar_index = 0
        self._bars: list[dict[str, object]] = []
        self._order_intents: list[dict[str, object]] = []
        self._risk_decisions: list[dict[str, object]] = []
        self._fills: list[dict[str, object]] = []
        self._trades: list[dict[str, object]] = []
        self._active_intent_index: int | None = None
        self._callback_fill: dict[str, object] | None = None
        self._cumulative_slippage = Decimal("0")
        self._peak_equity = INITIAL_CASH
        self._last_close_equity = INITIAL_CASH
        self._halt_reasons: tuple[str, ...] = ()
        self._rejected_count = 0

    def on_data(self, data: Slice) -> None:
        if not data.contains_key(self._symbol) or data[self._symbol] is None:
            return
        if self._bar_index >= len(self._fixture_bars):
            raise RuntimeError("LEAN delivered more parity rows than the prevalidated fixture")
        point = data[self._symbol]
        expected = self._fixture_bars[self._bar_index]
        if self.time.date() != expected.session:
            raise RuntimeError("LEAN algorithm time differs from the expected parity session")
        if point.session_timestamp != expected.timestamp:
            raise RuntimeError("LEAN parity row order differs from the prevalidated fixture")

        point.value = float(expected.open)
        self._security.set_market_price(point)
        pending = self._signal_state.begin_bar(expected.timestamp)
        if pending is not None and not self._halt_reasons:
            self._execute_pending(pending, expected)

        point.value = float(expected.close)
        self._security.set_market_price(point)
        target = self._signal_state.finish_bar(expected.timestamp, expected.close)
        self._record_close(expected, target)
        self._bar_index += 1

    def _execute_pending(self, pending: PendingSignal, bar: FixtureBar) -> None:
        if pending.timestamp >= bar.timestamp:
            raise RuntimeError("a parity signal cannot execute on its own bar")
        holdings = self.portfolio[self._symbol]
        current_quantity = Decimal(str(holdings.quantity))
        target_quantity = cost_aware_target_quantity(
            target_weight=pending.target_weight,
            reference_price=bar.open,
            current_quantity=current_quantity,
            cash=Decimal(str(self.portfolio.cash)),
        )
        quantity = target_quantity - current_quantity
        if quantity == 0:
            return
        projected_quantity = current_quantity + quantity
        if projected_quantity < 0:
            raise RuntimeError("parity order would create a short position")

        side = "buy" if quantity > 0 else "sell"
        absolute_quantity = abs(quantity)
        direction = Decimal("1") if quantity > 0 else Decimal("-1")
        estimated_price = bar.open * (Decimal("1") + direction * SLIPPAGE_BPS / Decimal("10000"))
        estimated_fee = absolute_quantity * estimated_price * FEE_BPS / Decimal("10000")
        notional = absolute_quantity * estimated_price
        intent_index = len(self._order_intents)
        intent = {
            "estimated_execution_price": canonical_decimal(estimated_price),
            "estimated_fee": canonical_decimal(estimated_fee),
            "execution_phase": "open",
            "execution_timestamp": bar.timestamp,
            "index": intent_index,
            "notional": canonical_decimal(notional),
            "quantity": canonical_decimal(absolute_quantity),
            "reference_price": canonical_decimal(bar.open),
            "side": side,
            "signal_timestamp": pending.timestamp,
            "symbol": SYMBOL,
            "target_weight": canonical_decimal(pending.target_weight),
        }
        self._order_intents.append(intent)

        risk, reasons = self._risk_decision(
            intent_index=intent_index,
            quantity=quantity,
            reference_open=bar.open,
            estimated_price=estimated_price,
            estimated_fee=estimated_fee,
            target_weight=pending.target_weight,
        )
        self._risk_decisions.append(risk)
        if reasons:
            self._rejected_count += 1
            return

        self._fill_model.set_reference_open(bar.open)
        self._active_intent_index = intent_index
        self._callback_fill = None
        self.market_order(
            self._symbol,
            float(quantity),
            asynchronous=False,
            tag=f"parity-v1-intent-{intent_index}",
        )
        self._active_intent_index = None
        if self._callback_fill is None:
            raise RuntimeError("synchronous parity order did not produce one fill callback")
        self._finalize_fill(intent_index, self._callback_fill)
        self._callback_fill = None

    def _risk_decision(
        self,
        *,
        intent_index: int,
        quantity: Decimal,
        reference_open: Decimal,
        estimated_price: Decimal,
        estimated_fee: Decimal,
        target_weight: Decimal,
    ) -> tuple[dict[str, object], tuple[str, ...]]:
        holdings = self.portfolio[self._symbol]
        current_quantity = Decimal(str(holdings.quantity))
        current_cash = Decimal(str(self.portfolio.cash))
        current_equity = Decimal(str(self.portfolio.total_portfolio_value))
        projected_quantity = current_quantity + quantity
        trade_value = abs(quantity) * estimated_price
        projected_cash = (
            current_cash - trade_value - estimated_fee
            if quantity > 0
            else current_cash + trade_value - estimated_fee
        )
        projected_equity = projected_cash + projected_quantity * reference_open
        asset_weight = (
            abs(projected_quantity * reference_open) / projected_equity
            if projected_equity > 0
            else Decimal("Infinity")
        )
        order_weight = trade_value / current_equity
        daily_loss = max(Decimal("0"), self._last_close_equity - current_equity)
        daily_loss = daily_loss / self._last_close_equity
        drawdown = max(Decimal("0"), self._peak_equity - current_equity) / self._peak_equity
        reduces_risk = abs(projected_quantity) < abs(current_quantity)

        reasons: list[str] = []
        if target_weight < 0:
            reasons.append("shorting_forbidden")
        if target_weight > MAX_POSITION_WEIGHT:
            reasons.append("max_position_weight")
        if asset_weight > MAX_TOTAL_EXPOSURE:
            reasons.append("max_total_exposure")
        if order_weight > MAX_ORDER_NOTIONAL_WEIGHT and not reduces_risk:
            reasons.append("max_order_notional")
        if projected_quantity < 0:
            reasons.append("negative_quantity")
        if projected_cash < 0:
            reasons.append("negative_cash")
        if daily_loss >= MAX_DAILY_LOSS and not reduces_risk:
            reasons.append("daily_loss")
        if drawdown >= MAX_DRAWDOWN and not reduces_risk:
            reasons.append("max_drawdown")
        reasons_tuple = tuple(reasons)
        return (
            {
                "index": len(self._risk_decisions),
                "intent_index": intent_index,
                "metrics": {
                    "asset_weight": canonical_decimal(asset_weight),
                    "daily_loss_pct": canonical_decimal(daily_loss),
                    "drawdown_pct": canonical_decimal(drawdown),
                    "order_weight": canonical_decimal(order_weight),
                    "projected_cash": canonical_decimal(projected_cash),
                    "projected_equity": canonical_decimal(projected_equity),
                    "reduces_risk": "1" if reduces_risk else "0",
                    "total_gross_weight": canonical_decimal(asset_weight),
                },
                "reasons": list(reasons_tuple),
                "status": "rejected" if reasons_tuple else "approved",
                "timestamp": self._fixture_bars[self._bar_index].timestamp,
            },
            reasons_tuple,
        )

    def on_order_event(self, order_event) -> None:
        if order_event.status == OrderStatus.PARTIALLY_FILLED:
            raise RuntimeError("partial fills are outside the v1 parity contract")
        if order_event.status != OrderStatus.FILLED:
            return
        if self._active_intent_index is None or self._callback_fill is not None:
            raise RuntimeError("unexpected parity fill callback")
        self._callback_fill = {
            "fee": Decimal(str(order_event.order_fee.value.amount)),
            "fill_price": Decimal(str(order_event.fill_price)),
            "quantity": Decimal(str(order_event.fill_quantity)),
        }

    def _finalize_fill(self, intent_index: int, callback: dict[str, object]) -> None:
        intent = self._order_intents[intent_index]
        fill_price = Decimal(str(callback["fill_price"]))
        signed_quantity = Decimal(str(callback["quantity"]))
        absolute_quantity = abs(signed_quantity)
        fee = Decimal(str(callback["fee"]))
        reference_price = Decimal(intent["reference_price"])
        slippage_cost = abs(fill_price - reference_price) * absolute_quantity
        self._cumulative_slippage += slippage_cost
        fill_index = len(self._fills)
        self._fills.append(
            {
                "execution_phase": "open",
                "execution_price": canonical_decimal(fill_price),
                "fee": canonical_decimal(fee),
                "index": fill_index,
                "intent_index": intent_index,
                "quantity": canonical_decimal(absolute_quantity),
                "reference_price": canonical_decimal(reference_price),
                "side": intent["side"],
                "slippage_cost": canonical_decimal(slippage_cost),
                "symbol": SYMBOL,
                "timestamp": intent["execution_timestamp"],
            }
        )
        holdings = self.portfolio[self._symbol]
        realized_delta = Decimal("0")
        if intent["side"] == "sell":
            realized_delta = Decimal(str(holdings.last_trade_profit))
        self._trades.append(
            {
                "average_cost_after": canonical_decimal(holdings.average_price),
                "fill_index": fill_index,
                "fill_timestamp": intent["execution_timestamp"],
                "index": len(self._trades),
                "quantity": canonical_decimal(absolute_quantity),
                "realized_pnl_delta": canonical_decimal(realized_delta),
                "resulting_cash": canonical_decimal(self.portfolio.cash),
                "resulting_quantity": canonical_decimal(holdings.quantity),
                "side": intent["side"],
                "signal_timestamp": intent["signal_timestamp"],
                "symbol": SYMBOL,
                "target_weight": intent["target_weight"],
            }
        )

    def _record_close(self, bar: FixtureBar, target: Decimal) -> None:
        holdings = self.portfolio[self._symbol]
        equity = Decimal(str(self.portfolio.total_portfolio_value))
        start_of_day_equity = self._last_close_equity
        daily_pnl = equity - start_of_day_equity
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = max(Decimal("0"), self._peak_equity - equity) / self._peak_equity
        daily_loss = max(Decimal("0"), -daily_pnl) / start_of_day_equity
        reasons: list[str] = []
        if daily_loss >= MAX_DAILY_LOSS:
            reasons.append("daily_loss")
        if drawdown >= MAX_DRAWDOWN:
            reasons.append("max_drawdown")
        if reasons and not self._halt_reasons:
            self._halt_reasons = tuple(reasons)

        position_value = Decimal(str(holdings.holdings_value))
        unrealized_profit = parity_money(
            Decimal(str(holdings.quantity)) * (bar.close - Decimal(str(holdings.average_price)))
        )
        exposure = abs(position_value) / equity if equity > 0 else Decimal("0")
        self._bars.append(
            {
                "average_cost": canonical_decimal(holdings.average_price),
                "cash": canonical_decimal(self.portfolio.cash),
                "close": canonical_decimal(bar.close),
                "cumulative_fees": canonical_decimal(self.portfolio.total_fees),
                "cumulative_slippage": canonical_decimal(self._cumulative_slippage),
                "daily_pnl": canonical_decimal(daily_pnl),
                "drawdown": canonical_decimal(drawdown),
                "equity": canonical_decimal(equity),
                "exposure_pct": canonical_decimal(exposure),
                "halted": bool(self._halt_reasons),
                "high": canonical_decimal(bar.high),
                "index": len(self._bars),
                "low": canonical_decimal(bar.low),
                "open": canonical_decimal(bar.open),
                "peak_equity": canonical_decimal(self._peak_equity),
                "position_market_value": canonical_decimal(position_value),
                "quantity": canonical_decimal(holdings.quantity),
                "realized_pnl": canonical_decimal(holdings.profit),
                "start_of_day_equity": canonical_decimal(start_of_day_equity),
                "symbol": SYMBOL,
                "target_weight_for_next_bar": canonical_decimal(target),
                "timestamp": bar.timestamp,
                "unrealized_pnl": canonical_decimal(unrealized_profit),
                "volume": canonical_decimal(bar.volume),
            }
        )
        self._last_close_equity = equity

    def _build_trace(self, engine_version: str) -> dict[str, object]:
        if len(self._bars) != EXPECTED_ROW_COUNT:
            raise RuntimeError("LEAN did not observe all eight prevalidated parity bars")
        if len(self._fills) != len(self._trades):
            raise RuntimeError("LEAN parity fill and trade observations disagree")
        final_pending = self._signal_state.pending
        if final_pending is None or final_pending.timestamp != self._bars[-1]["timestamp"]:
            raise RuntimeError("final parity signal was not retained for explicit expiry")
        exposures = [Decimal(str(bar["exposure_pct"])) for bar in self._bars]
        drawdowns = [Decimal(str(bar["drawdown"])) for bar in self._bars]
        turnover = (
            sum(
                (
                    Decimal(str(fill["execution_price"])) * Decimal(str(fill["quantity"]))
                    for fill in self._fills
                ),
                Decimal("0"),
            )
            / INITIAL_CASH
        )
        ending_equity = Decimal(str(self._bars[-1]["equity"]))
        final_timestamp = str(self._bars[-1]["timestamp"])
        creates_intent = any(
            intent["signal_timestamp"] == final_timestamp for intent in self._order_intents
        )
        creates_fill = any(trade["signal_timestamp"] == final_timestamp for trade in self._trades)
        return {
            "assumptions": _assumptions(),
            "bars": self._bars,
            "contract": {
                "contract_name": CONTRACT_NAME,
                "contract_sha256": CONTRACT_SHA256,
                "contract_version": CONTRACT_VERSION,
                "scenario_manifest_sha256": SCENARIO_MANIFEST_SHA256,
                "scenario_schema_sha256": SCENARIO_SCHEMA_SHA256,
                "trace_schema_sha256": TRACE_SCHEMA_SHA256,
            },
            "engine": {"name": "quantconnect_lean", "version": engine_version},
            "fills": self._fills,
            "final_bar": {
                "creates_fill": creates_fill,
                "creates_intent": creates_intent,
                "pending_signal_unfilled": not creates_intent and not creates_fill,
                "target_weight": self._bars[-1]["target_weight_for_next_bar"],
                "timestamp": final_timestamp,
            },
            "order_intents": self._order_intents,
            "provenance": "lean_engine_observation",
            "risk_decisions": self._risk_decisions,
            "scenario": {
                "bar_count": len(self._bars),
                "end_timestamp": self._bars[-1]["timestamp"],
                "fixture": FIXTURE_NAME,
                "fixture_sha256": FIXTURE_SHA256,
                "normalized_bars_sha256": NORMALIZED_BARS_SHA256,
                "scenario_id": SCENARIO_ID,
                "start_timestamp": self._bars[0]["timestamp"],
                "symbol": SYMBOL,
                "timeframe_seconds": TIMEFRAME_SECONDS,
            },
            "schema_version": TRACE_SCHEMA_VERSION,
            "strategy": {
                "configuration": {
                    "fast_window": FAST_WINDOW,
                    "slow_window": SLOW_WINDOW,
                    "target_weight": "0.1",
                },
                "name": "moving_average",
            },
            "summary": {
                "average_exposure": canonical_decimal(
                    sum(exposures, Decimal("0")) / len(exposures)
                ),
                "ending_equity": canonical_decimal(ending_equity),
                "estimated_slippage_cost": canonical_decimal(self._cumulative_slippage),
                "halt_reasons": list(self._halt_reasons),
                "max_drawdown": canonical_decimal(max(drawdowns)),
                "max_exposure": canonical_decimal(max(exposures)),
                "number_of_fills": len(self._fills),
                "realized_pnl": self._bars[-1]["realized_pnl"],
                "rejected_order_count": self._rejected_count,
                "risk_halt_triggered": bool(self._halt_reasons),
                "starting_cash": "100000",
                "total_fees_paid": self._bars[-1]["cumulative_fees"],
                "total_return": canonical_decimal((ending_equity - INITIAL_CASH) / INITIAL_CASH),
                "turnover": canonical_decimal(turnover),
                "unrealized_pnl": self._bars[-1]["unrealized_pnl"],
            },
            "trades": self._trades,
        }

    def on_end_of_algorithm(self) -> None:
        if self.transactions.get_open_orders(self._symbol):
            raise RuntimeError("parity backtest ended with an unexpected open order")
        engine_version = str(Globals.version)
        if not LEAN_VERSION_PATTERN.fullmatch(engine_version):
            raise RuntimeError("LEAN runtime version must be a dotted numeric value")
        self.debug(canonical_trace_line(self._build_trace(engine_version)))
