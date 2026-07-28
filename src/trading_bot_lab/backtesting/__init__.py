"""Local CSV backtesting utilities.

These helpers are for deterministic local research only. They do not connect to
brokers, exchanges, paid data vendors, or live-trading services.
"""

from trading_bot_lab.backtesting.csv_data import (
    CsvDataConfig,
    GapPolicy,
    MissingVolumePolicy,
    PriceBar,
    load_market_data_csv,
    load_price_bars_csv,
)
from trading_bot_lab.backtesting.engine import (
    BacktestConfig,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    BenchmarkComparison,
    BenchmarkSummary,
    EquityPoint,
    RiskRejection,
    SimulationEngine,
    build_market_data_metadata,
    run_backtest,
    run_moving_average_backtest,
    validate_simulation_bars,
)
from trading_bot_lab.backtesting.moving_average import MovingAverageStrategy, NoTradeStrategy
from trading_bot_lab.backtesting.reports import (
    export_equity_csv,
    export_json_report,
    export_rejected_intents_csv,
    export_risk_events_csv,
    export_trades_csv,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestSummary",
    "BacktestTrade",
    "BenchmarkComparison",
    "BenchmarkSummary",
    "CsvDataConfig",
    "EquityPoint",
    "GapPolicy",
    "MissingVolumePolicy",
    "MovingAverageStrategy",
    "NoTradeStrategy",
    "PriceBar",
    "RiskRejection",
    "SimulationEngine",
    "build_market_data_metadata",
    "export_equity_csv",
    "export_json_report",
    "export_rejected_intents_csv",
    "export_risk_events_csv",
    "export_trades_csv",
    "load_market_data_csv",
    "load_price_bars_csv",
    "run_backtest",
    "run_moving_average_backtest",
    "validate_simulation_bars",
]
