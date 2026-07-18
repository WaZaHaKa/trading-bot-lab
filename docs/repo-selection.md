# Repository selection notes

## Initial decision

Current status: start with the **free local Python CSV backtesting harness**
because LEAN CLI work is paused while the owner avoids paid QuantConnect
organization requirements.

Keep **QuantConnect LEAN** as a later candidate because the project may still
need both stocks and crypto in one framework after the no-cost MVP is working.

## Repository comparison summary

| Repo | Best use | Initial decision |
|---|---|---|
| Local Python CSV harness | Free, local, synthetic/demo-data smoke tests | Use first now |
| QuantConnect LEAN | Cross-asset stocks + crypto, backtest to paper/live workflow | Paused for later |
| Freqtrade | Crypto directional strategies and FreqAI | Consider later for crypto-only branch |
| Hummingbot | Crypto market making and exchange connector breadth | Consider later if strategy requires it |
| NautilusTrader | Event-driven, lower-latency, microstructure-aware systems | Consider later for advanced execution |
| Backtrader | Lightweight research backtesting | Useful for experiments, not primary core |
| Qlib | AI-oriented quant research | Consider after baseline strategies work |
| FinRL / SB3 / RLlib | Reinforcement learning experiments | Research only, not production orders |

## Why not start hybrid?

A hybrid system creates extra complexity immediately:

- multiple data models,
- multiple backtest assumptions,
- multiple execution semantics,
- more deployment paths,
- more monitoring requirements,
- and harder PnL reconciliation.

Start with one engine and add more only when needed.
