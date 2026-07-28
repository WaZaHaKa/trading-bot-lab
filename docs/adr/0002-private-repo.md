# ADR 0002 — Keep repository private initially

## Status

Superseded by ADR 0008 on 2026-07-28.

## Context

A trading repository may contain strategy IP, research notebooks, logs, configuration, reports, and accidental secrets.

## Decision

Keep the repository private while researching and building.

This was the initial decision. The repository is now public; the original risk
analysis remains relevant, but privacy can no longer be treated as a control.

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
