# Known limitations

- Cloud authentication, organization membership, and project linkage remain
  external operator state; repository tests cannot prove them.
- The local parity operator is intentionally limited to Linux, LEAN CLI
  `1.0.227`, one immutable `linux/amd64` image, and the explicitly validated
  user-owned rootless Docker daemon. Windows runs contract tests but refuses
  local Docker execution.
- Host-side HTTP/HTTPS isolation uses a process-local failing proxy because an
  unprivileged network namespace is unavailable on the authorized host. It is
  validated immediately before execution; the engine container independently
  uses `network_mode=none`.
- LEAN cloud data and engine versions can change independently of this source.
- The completed cloud baseline covers one daily US equity using QuantConnect
  SPY data, not crypto or a multi-asset portfolio.
- The successful cloud runs establish engine, synchronization, and public
  source/configuration validation only. They did not execute the committed
  synthetic parity fixture.
- Execution-timing and numerical-accounting parity both remain
  `pending_identical_data_execution`.
- Daily data cannot enforce or prove intraday daily-loss/stop behavior.
- Cash-account settlement, exchange calendars, lot sizes, fills, and corporate
  actions differ from the local simulator.
- The local engine is single-symbol and does not model dividends, splits,
  delistings, liquidity, partial fills, market impact, exchange calendars,
  borrow, funding, or multi-currency settlement.
- Synthetic parity data checks semantics only and cannot validate market-data
  quality or a strategy edge.
- A sanitized comparator fixture is not a LEAN engine observation.
- The committed fixture can be validated and copied byte-for-byte to the
  ignored LEAN custom-data path, and the dedicated project plus normalized v1
  observation producer and extractor are implemented. They have not been
  executed or verified by a LEAN engine.
- Neither the default local-file transport nor the explicit Object Store
  transport has processed the fixture in LEAN. No Object Store upload, download,
  or read was attempted in this implementation sprint.
- No real `TRADING_BOT_LAB_LEAN_PARITY_V1:` line or
  `lean_engine_observation` exists. Parser and comparator fixtures prove only
  deterministic validation behavior, not LEAN timing or accounting.
- Observation extraction validates canonical structure, content binding, and
  the claimed engine/provenance state; it is not cryptographic runtime
  attestation. The operator must preserve and review the actual ignored LEAN
  log and invocation context before treating an extracted trace as evidence.
- The runtime LEAN version remains execution-time evidence and cannot be filled
  from source inspection or the earlier SPY cloud record.
- The compiler warning category `discouraged_exception_handling` remains in
  both cloud projects. It was non-fatal for these runs but has not been removed.
- Project IDs, backtest IDs, URLs, account metadata, and raw cloud output are
  intentionally absent from the tracked sanitized record. Exact ignored local
  evidence is bound only by non-reversible SHA-256 digests.
- Portable Draft 2020-12 cannot require a timestamp string's date portion to
  equal a sibling `execution_date_utc` value. The public schema enforces canonical
  UTC-second shape; the typed normalizer enforces the cross-field equality and is
  authoritative for loading, serialization, and writes.
- The Moving Average ending-equity observation is not a profitability,
  robustness, or strategy-quality claim.
- No optimization, walk-forward result, Monte Carlo result, profitability
  claim, broker paper deployment, or live deployment exists in this sprint.
- The repository is public but has no selected open-source license.

Cloud and local backtests are hypothetical, place no real orders, are not
financial advice, and do not predict future results.

The next safe milestone is an actual normalized LEAN trace over the committed
synthetic fixture followed by the existing content-bound comparison. Any
walk-forward work remains subsequent and separately reviewed.
