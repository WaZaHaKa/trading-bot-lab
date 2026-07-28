# LEAN getting started

## Active status

**LEAN active for cloud research/backtesting; live trading remains prohibited.**

The organization workspace is `../lean-workspace/`. The old `../lean/` tree is
preserved until migration verification. The local Python workflow remains an
independent oracle; see `local-backtesting.md`.

Do not add live brokerage credentials, exchange credentials, QuantConnect API
tokens, or paid data-vendor secrets to this repository.

## Prerequisites

- Python 3.11 or newer
- Docker Desktop or Docker Engine
- Git
- Optional: `make`

The LEAN CLI and Docker setup can vary by machine. If a command below fails,
fix the local toolchain first; do not work around it by adding credentials or
live-trading configuration to Git.

## Python package setup

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
python scripts\preflight_check.py
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest
python scripts/preflight_check.py
```

If `make` is available, the equivalent project checks are:

```bash
make check
```

## LEAN CLI

The repository virtual environment is the preferred execution context:

```bash
python -m pip install lean
.\.venv\Scripts\lean.exe --version
```

Keep global credentials in the user profile. Never copy `.lean/credentials`
into the repository. See `windows-lean-setup.md` for interactive login and the
reviewed one-time workspace bootstrap.

## Run cloud backtests

Run only after authentication, workspace validation, and repository preflight:

```bash
Push-Location .\lean-workspace
& $LeanExe cloud push --project ".\Strategies\SkeletonBacktest"
& $LeanExe cloud backtest "Strategies/SkeletonBacktest" --push
```

Then run the long-only baseline:

```bash
& $LeanExe cloud push --project ".\Strategies\MovingAverageBaseline"
& $LeanExe cloud backtest "Strategies/MovingAverageBaseline" --push
Pop-Location
```

Expected behavior:

- `SkeletonBacktest` submits no orders.
- The reviewed `MovingAverageBaseline` cloud configuration is SPY, long-only,
  and caps target exposure at 10%; only the documented offline synthetic parity
  run may override the symbol to `PARITY`.
- Neither algorithm contains live-trading setup or brokerage credentials.

Cloud runs use data available to the organization. Do not run `lean data
download`, `lean backtest --download-data`, optimization, or a local
QuantConnect historical data provider. Local LEAN execution is optional and may
use only already-present or synthetic data under ignored workspace paths.

## Recording results

Record only brief non-promotional notes in `docs/strategy-log.md`:

- command run,
- date range,
- whether the backtest completed,
- any setup issue,
- risk or data caveat.

Do not commit LEAN result dumps from `lean/results/` or any report containing
account details.

See `lean-integration.md` and `qcc-guardrails.md` for the complete workflow.
