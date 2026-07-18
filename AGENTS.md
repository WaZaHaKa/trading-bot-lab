# AGENTS.md

## Mission

This repository is for building a safe, research-first trading bot lab for stocks and crypto.
The default objective is reproducible backtesting and paper trading, not real-money execution.

## Default operating mode

- Backtesting only unless a task explicitly says paper trading.
- Paper trading only after the backtest and risk checks pass.
- Live trading is disabled by default and must not be enabled by Codex or any automated agent.

## Non-negotiable safety rules

- Never add real broker, exchange, data-vendor, cloud, or OpenAI API keys.
- Never commit `.env`, logs, datasets, model binaries, reports with account details, or brokerage configuration containing secrets.
- Never enable real-money trading unless the human owner explicitly requests it in the current task.
- Never bypass, remove, weaken, or comment out risk checks.
- Never remove stop-loss, drawdown, exposure, freshness, kill-switch, or position-size logic.
- Never introduce lookahead bias. Features and labels must only use information available at the simulated decision time.
- Never let ML, AI, LLMs, or reinforcement-learning agents place orders directly. Models may emit signals; deterministic risk and execution layers decide what can become an order.
- Never use leverage, shorting, derivatives, futures, margin, or market making unless the human owner explicitly approves a scoped experiment.
- Never add dependencies that phone home, exfiltrate data, or require secrets without documenting why they are needed.
- Never make a production deployment change without tests, rollback notes, and a human approval step.

## Initial risk assumptions

- No leverage.
- No shorting.
- Max 10% portfolio weight per asset.
- Max 30% total gross exposure.
- Stop trading after 2% daily loss.
- Stop trading after 5% drawdown from peak equity.
- Reject orders when data is stale.
- Reject live orders unless `allow_live_trading` is explicitly true in a reviewed configuration.

## Required Codex response format

When changing code, respond in this order:

1. Plan
2. Assumptions and risks
3. Code changes
4. Tests or backtest instructions
5. Rollback notes

## Code quality rules

- Prefer small, typed, testable modules.
- Prefer pure functions for risk, sizing, features, and metrics.
- Keep broker/exchange adapters isolated from strategy logic.
- Keep strategy logic isolated from risk policy.
- Add or update tests for every behavior change.
- Add docs for every decision that changes architecture, risk, or deployment behavior.
- Keep generated research artifacts out of Git.

## Pull request checklist

Before marking work complete, verify:

- `make test` passes.
- No secrets are present.
- Risk checks were not weakened.
- The change does not enable live trading.
- Any new strategy has a documented backtest plan.
- Any model has dataset, feature, target, and validation notes.
- Any order-routing change has rollback notes.
