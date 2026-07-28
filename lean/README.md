# Preserved pre-activation LEAN files

This directory contains the original LEAN project drafts. Active migration work
lives under `../lean-workspace/Strategies/`.

## Current status

**LEAN active for cloud research/backtesting; live trading remains prohibited.**

Keep this tree unchanged until both migrated cloud projects complete and the
migration checklist is reviewed. It is not yet marked superseded. Do not add
QuantConnect credentials, broker credentials, exchange keys, paid data-vendor
keys, or live-trading config here.

## Historical goal

These drafts were created before the paid organization became available. They
are retained for migration comparison, not as the active cloud projects.

## Historical local workflow

```bash
lean init
lean backtest "lean/algorithms/SkeletonBacktest"
lean backtest "lean/algorithms/MovingAverageBaseline"
```

Do not run these legacy commands as activation evidence. See
`../docs/lean-integration.md` for the scoped active workflow.

## Safety

- Do not add live brokerage credentials.
- Do not add QuantConnect cloud credentials to Git.
- Do not commit downloaded LEAN market data.
- Do not commit LEAN backtest results, logs, or generated reports.
- Do not implement live trading in this starter phase.
