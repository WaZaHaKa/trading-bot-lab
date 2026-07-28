# Backtesting checklist

Before trusting a backtest, confirm each item.

## Current research paths

- Use LEAN cloud as the primary strategy backtest engine.
- Retain the local CSV harness as an independent regression/accounting oracle.
- Use only synthetic/demo committed data or ignored local data.
- Keep runs backtest-only.
- Run cloud commands manually and only for a named, reviewed project.
- Never download LEAN data, optimize, deploy live, or place real orders.
- Use historical paper replay only when explicitly selected; it remains local simulation.
- Route simulated orders through the risk policy.
- Set initial cash, fees, slippage, max position, and max exposure explicitly.
- Compare strategy output against buy-and-hold and cash/no-trade baselines.
- Keep generated reports under ignored `reports/` paths.
- Keep structured logs under ignored `logs/` and checkpoints under ignored `checkpoints/`.
- Record observations without making performance claims.

## LEAN activation and parity

- `lean whoami` succeeds in the exact PowerShell/CLI context used for the run.
- `lean-workspace/lean.json` is valid and credential-free.
- Project configuration contains no live, brokerage, secret, optimizer, or data-download setting.
- Cash account and one-times leverage are explicit.
- Signal, portfolio construction, risk, and execution remain separated.
- No order is generated before trailing-window readiness.
- A completed-bar target is eligible only at the next market open.
- Pending final-bar orders are canceled; no final fill is fabricated.
- Symbol and total exposure caps are independently checked.
- Daily-loss and drawdown guards latch and record their action.
- Normalized traces bind the parity fixture/contract hashes and engine provenance.
- Every cross-engine divergence is within a named tolerance or explicitly documented.

## Data correctness

- Timestamps are timezone-aware or explicitly UTC.
- Naive timestamps are rejected; `date` is explicitly midnight UTC.
- Input is already strictly ascending; duplicate timestamps are rejected rather than repaired.
- Equities use appropriate adjusted/unadjusted prices for the task.
- Crypto symbols map to the correct exchange and quote currency.
- Missing data is handled consistently.
- Required CSV columns are present before the run starts.
- Headers have no blanks, whitespace variants, or duplicates; rows have no extra cells.
- Duplicated dates are rejected.
- Unsorted dates are rejected.
- Non-positive or non-finite prices are rejected.
- Empty or NaN OHLCV values are rejected when OHLCV columns are present.
- Large calendar gaps are reviewed or rejected.
- The strategy never sees future bars.
- The strategy sees no more than the configured bounded trailing history.
- Close-generated targets execute only at the next bar open.
- Every simulation bar has a valid open before state mutation begins.
- Final-bar targets expire without an intent or fill.
- Fill records identify the execution-bar timestamp and `open` execution phase.

## Cost assumptions

- Fees are included.
- Minimum fees are included when relevant.
- Slippage is included.
- Buy and sell slippage both move price against the portfolio.
- Post-cost target and exposure weights remain within configured limits.
- Spread assumptions are included for intraday strategies.
- Market impact is considered for larger orders.
- Borrow, funding, and margin costs are excluded only if the strategy does not use them.

## Validation

- In-sample and out-of-sample periods are separated.
- Walk-forward tests are run.
- Parameters are not repeatedly tuned on the final holdout.
- The strategy is compared to buy-and-hold and cash benchmarks.
- Buy-and-hold applies documented warm-up, fee, slippage, precision, residual-cash,
  close-marking, and no-final-sale assumptions.
- Performance is checked across regimes.

## Risk metrics

Implemented minimum report fields:

- total return,
- benchmark return,
- max drawdown,
- number of trades,
- turnover,
- exposure over time,
- total fees paid,
- estimated slippage cost,
- average exposure,
- max exposure,
- whether a risk halt was triggered,
- buy-and-hold comparison,
- cash/no-trade comparison,
- realized and unrealized PnL,
- rejected-intent and warning counts,
- typed halt state and reasons.
- input content hash, safe filename, and explicit benchmark methodology.
- UTC start-of-day equity, daily PnL, and peak equity in the equity ledger.

Annualized return/volatility, Sharpe, Sortino, Calmar, win rate, average trade,
worst day/trade, and drawdown duration remain future metrics. Do not add them
until calendars, formulas, annualization, minimum samples, and edge cases are
documented and tested.

## Promotion gate

A strategy is not a paper-trading candidate unless:

- it survives costs,
- it survives reasonable parameter perturbations,
- it does not rely on a single lucky trade,
- it has clear risk controls,
- and the paper-trading monitoring plan is written.
