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
- Genuine local execution-timing parity passed, but numerical accounting/risk
  parity failed on three exit-decision risk ratios. Overall parity is failed.
- Daily data cannot enforce or prove intraday daily-loss/stop behavior.
- Cash-account settlement, exchange calendars, lot sizes, fills, and corporate
  actions differ from the local simulator.
- The local engine is single-symbol and does not model dividends, splits,
  delistings, liquidity, partial fills, market impact, exchange calendars,
  borrow, funding, or multi-currency settlement.
- Synthetic parity data checks semantics only and cannot validate market-data
  quality or a strategy edge.
- A sanitized comparator fixture is not a LEAN engine observation.
- The default local-file transport processed the committed fixture in a genuine
  network-isolated LEAN `2.5.0.0` run. The explicit Object Store transport remains
  unexecuted; no Object Store upload, download, or read was attempted.
- A real ignored `TRADING_BOT_LAB_LEAN_PARITY_V1:` message and validated
  `lean_engine_observation` now exist. The tracked record contains only sanitized
  classifications and non-reversible digests, not the raw message or trace.
- The final comparison passed 15 of 16 dimensions. LEAN valued the exit-decision
  risk snapshot at the current row close rather than the next-bar open used by
  the local oracle, so three risk ratios exceeded tolerance even though both
  engines approved the risk-reducing exit.
- The adapter now constructs the open-phase risk snapshot explicitly, and
  offline regressions reproduce and eliminate the close-mark mismatch. No
  corrected genuine LEAN observation exists yet, so the historical failed
  classification remains authoritative.
- Observation extraction validates canonical structure, content binding, and
  the claimed engine/provenance state; it is not cryptographic runtime
  attestation. The operator must preserve and review the actual ignored LEAN
  log and invocation context before treating an extracted trace as evidence.
- The local parity runtime version is observed as LEAN `2.5.0.0`; it is separate
  from the earlier cloud-validation engine version.
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

The next safe milestone is complete local validation, passing Ubuntu and Windows
CI for the correction commit, and then one authorized genuine comparison. The
five prior executions remain permanent history; batch
`open-phase-risk-correction-1` permits at most two additional executions and a
maximum cumulative count of seven, with no image pull. Any walk-forward work
remains subsequent and separately reviewed.
