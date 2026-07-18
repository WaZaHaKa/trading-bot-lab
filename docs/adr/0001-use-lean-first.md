# ADR 0001 — Use LEAN first

## Status

Superseded by ADR 0003.

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

LEAN folders are preserved, but LEAN CLI work is paused while the owner avoids
paid QuantConnect organization requirements. See ADR 0003 and
`docs/lean-paused.md`.
