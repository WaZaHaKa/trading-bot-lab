# Codex workflow

Use Codex as a coding assistant, reviewer, and test generator. Do not use Codex as an autonomous trading-system operator.

## Recommended workflow

1. Create a small Git branch.
2. Give Codex one scoped task.
3. Require a plan before changes.
4. Require tests for behavior changes.
5. Run `make test` and `make preflight`.
6. Review the diff manually.
7. Merge only after risk-sensitive changes are understood.

## First prompt to use

```text
We are starting a private trading bot research repository named trading-bot-lab.

Read AGENTS.md first and obey it.

Goal:
Prepare the free local CSV backtesting workflow for backtesting only.

Requirements:
- Do not add secrets.
- Do not enable live trading.
- Do not add paid services.
- Do not add AI, ML, neural networks, or reinforcement learning.
- Verify the Python package tests pass.
- Run `python scripts/run_local_backtest.py` against synthetic/demo data.
- Confirm simulated orders go through the risk-policy module.
- Output a plan, risks, code/docs changes, tests, and rollback notes.
```

## Useful review prompt

```text
Review this diff as if it could affect a trading bot.

Focus on:
- lookahead bias
- order-sizing errors
- stale-data risk
- missing kill-switch checks
- live-trading enablement
- accidental secrets
- missing tests
- silent exception handling
- retry storms or rate-limit issues

Return:
1. Critical issues
2. Medium issues
3. Suggested tests
4. Safe-to-merge verdict
```

## Backtest validation prompt

```text
Given this strategy and backtest report, evaluate whether the apparent edge may be due to:
- overfitting
- lookahead bias
- survivorship bias
- unrealistic fees or slippage
- narrow market regime
- unstable position sizing

Then propose:
- five ablation tests
- three walk-forward windows
- paper-trading acceptance criteria
- rollback conditions
```
