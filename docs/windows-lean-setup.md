# Windows PowerShell LEAN setup

Use the repository virtual environment's CLI so version and execution context
are explicit.

```powershell
$LeanExe = (Resolve-Path ".\.venv\Scripts\lean.exe").Path
& $LeanExe --version
& $LeanExe whoami
```

If `whoami` says the CLI is not logged in, run the interactive wizard yourself:

```powershell
& $LeanExe login
```

Enter the user ID and API token only in the masked LEAN prompts. Never pass
either with `--user-id`, `--api-token`, or `--show-secrets`; never paste them
into this repository or a Codex task.

## Workspace initialization

`lean init` requires an authenticated paid organization and downloads LEAN's
workspace configuration plus sample data. Because this repository prohibits
automatic data acquisition, run it only as an explicit one-time workspace
bootstrap after operator review. Initialize in the empty `_lean_init_tmp/`
directory, inspect the result, then copy the credential-free `lean.json` into
`lean-workspace/`. Keep generated `data/` and `storage/` ignored; do not commit
them and do not invoke any additional data command.

The repository projects are maintained directly under
`lean-workspace/Strategies/`; do not replace them with the CLI's default
100%-allocated starter project.

## Safe verification

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python scripts\preflight_check.py
git diff --check
```

The cloud workflow is documented in `lean-integration.md`. Docker is optional
for this sprint, and local LEAN runs must never trigger a data download.

If Docker and a reviewed initialized workspace are already healthy, the
committed synthetic fixture can be prepared offline with:

```powershell
python scripts\prepare_lean_parity_data.py
```

The command creates only ignored synthetic files and refuses to replace
differing files. See `cross-engine-parity.md` before attempting a local LEAN
run, and use `--no-update` with no historical-provider or download option.
