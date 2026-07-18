# Monitoring

Lightweight local observability is implemented for backtests and historical
paper replay through optional rotated JSON-lines logs. External monitoring and
alerting remain unimplemented.

Future metrics:

- strategy PnL,
- exposure,
- drawdown,
- order rejects,
- API errors,
- data freshness,
- model version,
- model drift,
- kill-switch state.
