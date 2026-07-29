# Scripts

Small local utility scripts only.

- `preflight_check.py`: validates that required starter files exist and obvious live-trading flags are not enabled.
- `print_tree.py`: prints the repository tree.
- `run_local_backtest.py`: delegates to the package's deterministic `backtest` command.
- `run_historical_paper_replay.py`: canonical entirely local `paper-replay` entry point.
- `run_paper_replay.py`: compatible alias for the same local replay command.
- `export_local_parity.py`: exports a normalized v1 local-oracle trace offline.
- `prepare_lean_parity_data.py`: converts only the committed v1 synthetic fixture into
  ignored LEAN daily-equity files without network activity or overwrite.
- `compare_lean_parity.py`: validates and compares two existing v1 traces offline.
- `run_lean_parity_local.py`: enforces the separately authorized pinned,
  network-isolated local LEAN parity phases.
- `run_walk_forward_v1.py`: exposes one typed, offline walk-forward operator.
  Its phases are `plan`, `validate`, `print-cloud-commands`, `extract`,
  `aggregate`, `evidence`, `extract-result`, `aggregate-result`, and
  `evidence-result`; the default is the read-only `plan`.

The walk-forward command printer still emits the exact five named cloud
backtests for the closed `spy-2021` through `spy-2025` fold set, but it does not
execute a process and there is no cloud-run phase. Those five runs are complete
and must not be rerun. The extract and aggregate phase pairs accept bounded
regular files, reject symlink/reparse paths, and write atomically only below
ignored `reports/walk-forward/v1`; aggregate output cannot replace a fold
input. Aggregation requires all five observations. The reviewed Download
Results aggregate is tracked separately at
`contracts/walk-forward/v1/2026-07-29-result-aggregate.json`; raw downloads and
working observations remain private and ignored. No script in this directory
automates optimization, data download/upload, Object Store, broker/exchange
access, paper trading, or live trading.
