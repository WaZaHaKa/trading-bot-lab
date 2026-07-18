# Model risk policy

AI/ML is allowed in this project only as a controlled research and signal-generation component.

No AI/ML output is consumed by the active engine. `ModelForecast` is a disabled
schema boundary only; it does not connect to order creation.

## Model classes

Treat these as separate model classes:

- alpha models,
- volatility models,
- regime models,
- position-sizing models,
- execution models,
- and LLM-generated code changes.

## Required model metadata

Every model experiment should record:

- dataset version,
- feature list,
- target definition,
- training window,
- validation window,
- holdout window,
- preprocessing steps,
- leakage checks,
- metrics,
- limitations,
- and intended use.

## Production rule

Models must not submit orders directly.

Allowed model output:

```text
symbol, timestamp, signal, confidence, horizon, model_version
```

Not allowed model output:

```text
place this live order immediately
```

Before a future forecast can influence even a shadow target, it must pass:

- confidence and numeric-range validation,
- data and prediction freshness validation,
- model and schema version validation,
- deterministic cash, exposure, and circuit-breaker checks,
- reproducible shadow and paper evaluation,
- and explicit human approval before any production consideration.

## Promotion stages

1. Notebook experiment.
2. Reproducible training script.
3. Backtest-only signal model.
4. Shadow paper signal.
5. Paper-trading candidate.
6. Production consideration only after a separate security milestone and human review.

## Drift and rollback

A model should be removed from paper or live consideration when:

- feature drift is severe,
- prediction distribution changes sharply,
- performance falls outside acceptance bands,
- data freshness fails,
- or a newer code/data version cannot reproduce prior results.
