# Historical paper replay

Historical paper replay is an entirely local simulation. It is not a broker
paper account and makes no network calls.

## Reproducibility inputs

A session is determined by:

- validated CSV and metadata,
- strategy name and configuration,
- backtest/cost/precision configuration,
- deterministic risk configuration,
- replay speed (scheduling only),
- engine version.

No randomness exists in the current simulator. Session IDs are stable hashes of
engine, strategy, risk, simulation configuration, and input metadata.

## Future-row protection

`HistoricalReplaySession.step()` delivers exactly one bar to the shared
`SimulationEngine`. The strategy receives an immutable tuple containing only
bars already processed. There is no full-dataframe or cursor API at the strategy
boundary. A test records each visible prefix length and rejects mismatched/future
signal timestamps.

## Lifecycle

```text
created -> running -> paused -> running -> completed
                    \-> stopped
running/paused -> halted
```

Start, pause, resume, stop, completion, manual kill switch, and risk halt are
explicit typed transitions with deterministic event timestamps. Invalid state
transitions raise an error. The manual kill switch latches the shared engine halt.

## Commands

```powershell
python scripts\run_paper_replay.py
python scripts\run_paper_replay.py --csv-path data\local\SPY_daily.csv
python scripts\run_paper_replay.py --replay-speed-seconds 0.1
python scripts\run_paper_replay.py --pause-after-bars 5
python scripts\run_paper_replay.py --kill-switch-after-bars 8
python scripts\run_paper_replay.py `
  --export-json reports\paper-session.json `
  --log-jsonl logs\paper-session.jsonl
```

Replay speed is between 0 and 60 seconds per event; zero runs immediately.

## Observability

Optional JSON-lines logs contain session ID, strategy, symbol, event timestamp,
signal, order intent, risk decision, fill, position, equity, drawdown, halt, and
state transitions where applicable. `RotatingFileHandler` limits each file to
2 MB by default with two backups. Logs are ignored and must never contain
secrets or account data.

## Limits

Replay is synchronous, single-symbol, and historical. There is no broker clock,
order acknowledgement, partial fill, reconciliation, external pause control,
or process recovery. Those concerns remain out of scope until broader offline
testing and multi-asset simulation are mature.
