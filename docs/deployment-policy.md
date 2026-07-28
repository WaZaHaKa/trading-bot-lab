# Deployment policy

Deployment is intentionally out of scope for the starter repo.

QuantConnect cloud **backtesting** is an active research workflow. It is not a
live deployment and grants no authority to configure a brokerage or real order
route.

## Allowed now

- Local development.
- Local unit tests.
- Local backtesting.
- Local historical CSV paper replay.
- Local research notebooks.
- Two manually named, project-scoped LEAN cloud backtests after preflight.

Historical replay is not a deployment and has no external API or broker account.

## Not allowed yet

- Cloud live deployment or unattended cloud execution.
- Kubernetes.
- Production brokers.
- Live exchange API keys.
- Automated order-routing services.
- Public dashboards with account data.

## Future deployment gates

Before deployment exists, create:

- secret-management plan,
- environment separation plan,
- logging plan,
- monitoring plan,
- alert routing plan,
- kill-switch runbook,
- rollback runbook,
- and cost controls.
