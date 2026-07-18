# LEAN getting started

## Paused status

LEAN CLI work is paused because the attempted local workflow requires a paid
QuantConnect organization and the owner does not want to spend money right now.
Preserve the `lean/` folders for later, but use `docs/local-backtesting.md` for
the active free MVP.

This guide keeps LEAN usage local and backtest-only for Milestone 1.

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

## Install LEAN CLI locally

Install the LEAN CLI into the activated virtual environment:

```bash
python -m pip install lean
lean --version
```

Keep any LEAN CLI user configuration local. If `lean init` creates local config
or data folders, confirm they are ignored before committing.

## Run local backtests

Start with the no-trade skeleton:

```bash
lean backtest "lean/algorithms/SkeletonBacktest"
```

Then run the experimental moving-average baseline:

```bash
lean backtest "lean/algorithms/MovingAverageBaseline"
```

Expected behavior:

- `SkeletonBacktest` submits no orders.
- `MovingAverageBaseline` is SPY-only, long-only, and caps target exposure at 10%.
- Neither algorithm contains live-trading setup or brokerage credentials.

LEAN may require local market data before these backtests complete. Store local
data under `lean/data/`; it is ignored by Git. Do not commit downloaded data,
backtest result files, logs, or generated reports.

## Recording results

For Milestone 1, record only brief notes in `docs/strategy-log.md`:

- command run,
- date range,
- whether the backtest completed,
- any setup issue,
- risk or data caveat.

Do not commit LEAN result dumps from `lean/results/` or any report containing
account details.
