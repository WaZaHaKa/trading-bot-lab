# Security policy

## Secret handling

Never commit secrets.

Examples of secrets:

- broker API keys,
- exchange API keys,
- data-vendor keys,
- cloud credentials,
- wallet private keys or seed phrases,
- database passwords,
- SSH private keys,
- production webhook URLs,
- and account identifiers in logs.

Use `.env` locally only, and keep it ignored by Git. For anything beyond local research, use a real secret manager.

## API key rules

When keys are eventually created:

- prefer paper/sandbox keys first,
- use read-only keys where possible,
- disable withdrawals on exchange keys,
- restrict IPs if supported,
- use separate keys per environment,
- rotate keys after incidents,
- and delete unused keys.

## Repository rules

- Treat every tracked file, branch, pull request, and CI log as public.
- Do not commit datasets unless licenses allow it and the data is small enough.
- Do not commit generated model binaries.
- Do not commit brokerage account screenshots or logs.
- Do not publish strategy IP unintentionally.
- Do not load pickle/joblib artifacts or execute code from configuration.
- Do not use `shell=True`, string-built shell commands, or untrusted subprocess input.
- Do not add network clients to the active CSV/backtest/paper-replay path.
- Do not hardcode machine-specific user paths.
- Keep reports, logs, local data, notebook outputs, caches, package metadata,
  and model artifacts ignored.
- Keep global `.lean/` credentials, LEAN workspace data/Object Store content,
  cloud backtest output, optimizations, and live output outside Git.

## Active security boundary

The local Python package uses the standard library and reads only explicit
local CSV paths.
It contains no HTTP/socket client, broker/exchange adapter, secret loader, unsafe
deserializer, database, or arbitrary configuration evaluator. CLI arguments are
typed and unknown fields fail through `argparse`.

`scripts/preflight_check.py` verifies required safety files, disabled live flags,
likely credential assignments, unsafe code-execution patterns, user-specific
paths, network-client imports in active Python, and required ignore rules. It is
a defense-in-depth heuristic, not a replacement for secret scanning or review.

Structured logs serialize only engine-generated domain fields. They never read
environment variables or configuration secrets and rotate with bounded local size.

Manual LEAN CLI cloud commands are the only active network boundary. They use
credentials stored by the CLI outside the repository, are never run by CI, and
must remain project-scoped. Preflight reports only the path and category of a
credential finding; it never prints the suspected value.

## Codex and AI assistant rules

- Codex may write code and tests.
- Codex may not enable live trading.
- Codex may not add real credentials.
- Codex may not weaken risk limits without explicit human approval and an ADR.
- Codex-generated changes must be reviewed before merge.
