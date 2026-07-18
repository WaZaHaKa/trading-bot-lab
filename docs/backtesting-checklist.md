# Backtesting checklist

Before trusting a backtest, confirm each item.

## Current MVP path

- Use the local CSV harness while LEAN CLI is paused.
- Use only synthetic/demo committed data or ignored local data.
- Keep runs backtest-only.
- Use historical paper replay only when explicitly selected; it remains local simulation.
- Route simulated orders through the risk policy.
- Set initial cash, fees, slippage, max position, and max exposure explicitly.
- Compare strategy output against buy-and-hold and cash/no-trade baselines.
- Keep generated reports under ignored `reports/` paths.
- Record observations without making performance claims.

## Data correctness

- Timestamps are timezone-aware or explicitly UTC.
- Naïve timestamps are rejected; `date` is explicitly midnight UTC.
- Bars/trades are sorted and deduplicated.
- Equities use appropriate adjusted/unadjusted prices for the task.
- Crypto symbols map to the correct exchange and quote currency.
- Missing data is handled consistently.
- Required CSV columns are present before the run starts.
- Duplicated dates are rejected.
- Unsorted dates are rejected.
- Non-positive or non-finite prices are rejected.
- Empty or NaN OHLCV values are rejected when OHLCV columns are present.
- Large calendar gaps are reviewed or rejected.
- The strategy never sees future bars.
- Close-generated targets execute only at the next bar open.
- Missing next-open prices fail closed when a fill is due.

## Cost assumptions

- Fees are included.
- Minimum fees are included when relevant.
- Slippage is included.
- Spread assumptions are included for intraday strategies.
- Market impact is considered for larger orders.
- Borrow, funding, and margin costs are excluded only if the strategy does not use them.

## Validation

- In-sample and out-of-sample periods are separated.
- Walk-forward tests are run.
- Parameters are not repeatedly tuned on the final holdout.
- The strategy is compared to buy-and-hold and cash benchmarks.
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
