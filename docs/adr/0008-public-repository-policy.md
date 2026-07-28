# ADR 0008 - Treat the repository as public

## Status

Accepted on 2026-07-28. Supersedes ADR 0002.

## Context

The repository is publicly readable. Earlier documentation incorrectly
described it as private.

## Decision

Every tracked file, commit, branch, pull request, CI log, and generated diff is
treated as public information.

- Credentials and global LEAN state remain outside the repository.
- Licensed/downloaded market data, Object Store content, backtest output,
  optimization output, live output, reports, logs, and account-bearing files
  remain ignored.
- Numeric organization, project, local, cloud, and backtest identifiers are
  metadata rather than authentication secrets, but they are reviewed before
  publication and are never accepted as proof of authorization.
- A credential-free `lean-workspace/lean.json` may be tracked. The preflight
  scanner rejects secret-bearing keys or values without printing them.
- No open-source license has been selected. Public visibility does not by
  itself grant permission to copy, modify, or redistribute the code.

## Consequences

Repository preflight is defense in depth, not a substitute for GitHub secret
scanning, push protection, history review, or manual diff review.

