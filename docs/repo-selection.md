# Repository selection notes

## Initial decision

Current status: use **QuantConnect LEAN cloud** as the primary cross-asset
research/backtesting engine. Retain the **free local Python CSV harness** as an
independent deterministic timing, risk, and accounting oracle.

## Repository comparison summary

| Repo | Best use | Initial decision |
|---|---|---|
| Local Python CSV harness | Free, local, synthetic/demo-data oracle and smoke tests | Retain for parity |
| QuantConnect LEAN | Cross-asset stocks + crypto cloud backtests | Primary research engine |
| Freqtrade | Crypto directional strategies and FreqAI | Consider later for crypto-only branch |
| Hummingbot | Crypto market making and exchange connector breadth | Consider later if strategy requires it |
| NautilusTrader | Event-driven, lower-latency, microstructure-aware systems | Consider later for advanced execution |
| Backtrader | Lightweight research backtesting | Useful for experiments, not primary core |
| Qlib | AI-oriented quant research | Consider after baseline strategies work |
| FinRL / SB3 / RLlib | Reinforcement learning experiments | Research only, not production orders |

## Why the two-engine boundary is controlled

A hybrid system creates extra complexity immediately:

- multiple data models,
- multiple backtest assumptions,
- multiple execution semantics,
- more deployment paths,
- more monitoring requirements,
- and harder PnL reconciliation.

The engines do not share accounting or execution code. A versioned synthetic
contract and fail-closed comparator make their differences explicit. Live and
broker-paper workflows remain prohibited.
