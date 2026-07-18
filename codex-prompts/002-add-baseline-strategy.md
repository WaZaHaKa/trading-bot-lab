# Codex prompt - extend local baseline strategy

Use only after the local CSV sample backtest and risk-policy tests pass.

```text
Read AGENTS.md first and obey it.

Goal:
Extend the simple local CSV moving-average baseline for research backtesting only.

Strategy constraints:
- Daily bars.
- Synthetic/demo data or ignored local CSV data only.
- Long only.
- No leverage.
- No shorting.
- Target exposure must stay at or below 10% per asset.
- Max total exposure remains 30%.
- Stop new trades after 2% daily loss.
- Stop new trades after 5% drawdown.
- Do not enable live trading.

Requirements:
- Use the existing local backtesting harness.
- Use the existing risk-policy module.
- Add tests for every behavior change.
- Do not optimize parameters.
- Do not make performance claims.
- Do not add broker/exchange credentials or paid services.
- Output a plan first.
```
