# ADR 0007 - Use LEAN cloud as the primary research engine and retain the local oracle

## Status

Accepted on 2026-07-28.

## Context

The project now has access to a paid QuantConnect research organization. LEAN
can therefore provide the primary cross-asset strategy runtime for cloud
backtests without requiring licensed market data to be downloaded into this
public repository.

The existing Python CSV simulator remains valuable precisely because it is
small, deterministic, dependency-light, and independent of LEAN. Replacing it
would remove the accounting and timing oracle needed to explain cross-engine
differences.

## Decision

- LEAN cloud is the primary engine for new stock and crypto strategy research
  and backtesting.
- The local Python engine remains an independent regression, timing, risk, and
  accounting oracle. It is not a wrapper around LEAN and does not share LEAN's
  fill or accounting implementation.
- The active organization workspace lives under `lean-workspace/`; project
  source and credential-free configuration may be tracked.
- The former `lean/` tree remains preserved until both migrated projects have
  completed cloud backtests and the migration checklist has been reviewed.
- Cloud commands are manual, named, and project-scoped. CI never authenticates,
  pushes, backtests, optimizes, downloads data, or invokes a live command.
- Live trading, brokerage integrations, leverage, margin exposure, shorting,
  futures, derivatives, market making, optimization, and AI/ML/RL remain
  prohibited.
- No local QuantConnect dataset purchase or automatic download is allowed.

## Cross-engine contract

The shared contract is a versioned synthetic scenario and normalized event
schema, not shared execution code. Both engines must expose enough evidence to
compare bar visibility, signal time, next eligible execution, trade direction,
position state, fees, slippage, cash/equity, risk events, and final-bar
behavior.

Exact equality is not expected where LEAN intentionally models exchange
calendars, corporate actions, lot sizes, cash settlement, or different fill
semantics. Every accepted difference requires a named tolerance or documented
model exception; unexplained divergence fails parity.

## Consequences

Cloud results use QuantConnect data and LEAN reality models and are
hypothetical. Local results use explicit synthetic or user-supplied CSV data
and a simpler model. Neither result is a profitability claim or financial
advice.

Authentication remains in the operator's global `.lean` directory. No token,
password, key, or credential file may enter the repository.

## Next safe milestone

Walk-forward validation using LEAN cloud backtests and local parity checks.
