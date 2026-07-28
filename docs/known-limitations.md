# Known limitations

- Cloud authentication and organization membership are external operator state;
  repository tests cannot prove them.
- LEAN cloud data and engine versions can change independently of this source.
- The required cloud baseline currently covers one daily US equity, not crypto
  or a multi-asset portfolio.
- Daily data cannot enforce or prove intraday daily-loss/stop behavior.
- Cash-account settlement, exchange calendars, lot sizes, fills, and corporate
  actions differ from the local simulator.
- The local engine is single-symbol and does not model dividends, splits,
  delistings, liquidity, partial fills, market impact, exchange calendars,
  borrow, funding, or multi-currency settlement.
- Synthetic parity data checks semantics only and cannot validate market-data
  quality or a strategy edge.
- A sanitized comparator fixture is not a LEAN engine observation.
- The committed fixture can be converted to ignored LEAN-format data, but a
  LEAN-side normalized v1 trace producer has not yet been implemented or
  verified. The implementation host's Docker Linux engine was unavailable.
- No optimization, walk-forward result, Monte Carlo result, profitability
  claim, broker paper deployment, or live deployment exists in this sprint.
- The repository is public but has no selected open-source license.

Cloud and local backtests are hypothetical, place no real orders, are not
financial advice, and do not predict future results.

The next safe milestone is walk-forward validation using LEAN cloud backtests
and local parity checks.
