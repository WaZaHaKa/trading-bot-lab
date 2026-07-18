# Deployment policy

Deployment is intentionally out of scope for the starter repo.

## Allowed now

- Local development.
- Local unit tests.
- Local backtesting.
- Local historical CSV paper replay.
- Local research notebooks.

Historical replay is not a deployment and has no external API or broker account.

## Not allowed yet

- Cloud deployment.
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
