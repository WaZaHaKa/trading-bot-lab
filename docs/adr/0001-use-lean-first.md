# ADR 0001 — Use LEAN first

## Status

Superseded for the original MVP by ADR 0003. ADR 0007 later reactivates LEAN
cloud as the primary research/backtest engine while retaining the local oracle.

## Context

The project needs to research strategies across both stocks and crypto. Starting with multiple engines would add complexity before there is a validated edge.

## Decision

Use QuantConnect LEAN as the first engine for local backtesting and eventual paper trading.

## Consequences

Positive:

- One engine for equities and crypto.
- Strong backtesting workflow.
- Risk-management abstractions.
- Python support.

Negative:

- Crypto-specific workflows may be less specialized than Freqtrade or Hummingbot.
- Low-latency and L2/L3 market microstructure work may eventually need NautilusTrader.

## Follow-up

The original `lean/` folders remain preserved. Active projects now live under
`lean-workspace/`; live trading and the original paper-trading follow-up remain
prohibited. See ADR 0007.
