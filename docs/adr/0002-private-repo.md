# ADR 0002 — Keep repository private initially

## Status

Accepted.

## Context

A trading repository may contain strategy IP, research notebooks, logs, configuration, reports, and accidental secrets.

## Decision

Keep the repository private while researching and building.

## Consequences

Positive:

- Reduces accidental exposure of strategy ideas and sensitive artifacts.
- Allows safer iteration with Codex.
- Public release can be decided later.

Negative:

- No open-source community feedback during early development.

## Future option

Split the project later:

```text
trading-bot-core      public possible
trading-strategies    private
trading-research      private
```
