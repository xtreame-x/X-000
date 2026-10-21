# OpenAgent

A self-modifying agentic runtime in Python, with the 303-tool catalog from
`available_tools.md` implemented behind one registry. Built to run on **Windows**,
developed on Linux, with every OS difference isolated in one module.

## Status

| Checkpoint | Scope | Tools | State |
|---|---|---|---|
| 1 | Core 42 + Drive 11 + Claude Docs 5 + Three.js 2 + SketchUp 5 | **65** | implemented, tested |
| 2 | Vercel | 238 | not started |

```
$ python -m agent verify
counts: checkpoint1 65, checkpoint2 0, total 65
```

`verify --strict` exits non-zero until checkpoint 2 lands, so the gate cannot be
passed accidentally.

## Install (Windows)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Check your setup:

```powershell
python -m agent verify
python -m agent tools
python -m agent call view --args "{\"path\": \"README.md\"}"
```

## Run

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # or OPENAI_API_KEY
python -m agent "add a /health endpoint and run the tests"
python -m agent chat                       # interactive
python -m agent run "explain agent/loop.py" --max-steps 12 --verbose
```

Without credentials the tool layer still works directly:

```powershell
python -m agent call tool_search --args "{\"query\": \"drive\"}"
python -m agent call user_time_v0 --args "{}"
python -m agent schema --deferred --out schemas.json
```

## Configuration

All settings are environment variables; secrets are read on demand and never
written to disk, logged, or included in artifacts.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | LLM credentials (`AGENT_PROVIDER` forces one) |
| `AGENT_MODEL` | Model id (defaults per provider) |
| `AGENT_WORKSPACE` | Workspace root (defaults to cwd) |
| `AGENT_SHELL` | Shell for `bash_tool`: `pwsh`, `powershell`, `cmd`, `bash` |
| `AGENT_TIMEZONE` | Default IANA timezone for calendar tools |
| `AGENT_LATITUDE` / `AGENT_LONGITUDE` / `AGENT_LOCATION_LABEL` | Skip IP geolocation |
| `AGENT_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` | Enable real email sending |
| `GOOGLE_ACCESS_TOKEN` *(or `GOOGLE_CLIENT_ID` + `_CLIENT_SECRET` + `_REFRESH_TOKEN`)* | Google Drive |
| `SERPER_API_KEY` | Optional: Google-quality web/image search |
| `AGENT_SKETCHUP_MONTHLY_LIMIT` | Build quota for the SketchUp tools (default 50) |
| `AGENT_ALLOW_DESTRUCTIVE_COMMANDS` | Permit `rm -rf /`, `mkfs`, etc. (default off) |
| `AGENT_WINDOWS_PATH_RULES` | Force Windows path validation (on automatically on Windows) |

## Architecture

```
agent/
  platform_compat.py   # the ONLY place that knows about Windows
  config.py            # Settings, NotConfigured
  state.py             # durable JSON/JSONL state, atomic writes
  self_edit.py         # snapshots, syntax gate, CRLF-safe patching, reload
  tools/base.py        # @tool decorator -> pydantic schema from the signature
  runtime.py           # dispatch, deferred gating, error normalisation, audit
  llm.py               # Anthropic, OpenAI, Scripted providers (httpx only)
  loop.py              # model -> tools -> model until done
  cli.py               # python -m agent
  tools/*.py           # 12 modules, 65 tools
catalog/               # generated, validated 303-entry tool catalog
tests/                 # 126 tests
```

Tool JSON Schemas are **derived from the handler signature**, with descriptions
from Google-style docstrings. A handler and its advertised schema cannot drift
apart, and unknown arguments are rejected (`additionalProperties: false`).

## Windows specifics

* `bash_tool` keeps its documented name but dispatches to `pwsh` → `powershell` →
  `cmd`, forcing UTF-8 console output; timeouts kill the whole tree with
  `taskkill /T`.
* Edits are CRLF-safe: files are read LF-normalised, matched, then written back
  with their original line endings.
* `os.replace` is retried with exponential backoff, so an editor or antivirus
  holding the file open does not corrupt a self-edit.
* Reserved names (`CON`, `NUL`, `COM1`…), illegal characters, trailing dots and
  spaces, and `MAX_PATH` are rejected before writing.
* State locking is in-process (`threading.RLock`) plus atomic rename, because
  `fcntl` does not exist on Windows.

These rules are tested on Linux by forcing the platform flag
(`AGENT_WINDOWS_PATH_RULES=1`); see `tests/test_platform_compat.py`.

## Self-modification safety

* Every edit snapshots the file first (`state/snapshots/…`); `SelfEditor.restore()`
  reverts it.
* `.py` edits are compiled before they land and `.json` edits are parsed; invalid
  code never reaches disk.
* `.env*` and `state/` are not writable through tools.
* All edits are appended to `state/self_edits.jsonl`; every tool call to
  `state/audit.jsonl`.
* Due alarms and timers are collected with `agent.scheduler.drain_due(state)`;
  repeating alarms advance instead of being dropped.

## Limitations (verified, not assumed)

* Checkpoint 2 (238 Vercel tools) is not implemented; `tool_search` for Vercel
  returns no matches today.
* Network tools (`web_search`, `places_search`, `fetch_sports_data`, Drive) are
  implemented against documented HTTP endpoints but were **not** exercised
  against live services in this environment. They return structured errors on
  failure.
* `bash_tool` is not a sandbox. It blocks an obvious destructive-command list and
  confines `cwd` to the workspace, but a determined command can still do damage.
* Windows behaviour is enforced and unit-tested via the platform seam; it has not
  been executed on real Windows hardware here.
* Alarms and timers are persisted, not fired: the host must call
  `agent.scheduler.drain_due()` (checkpoint 2 work).

## Tests

```
python -m pytest        # 126 passed
ruff check agent tests --select F,E9
```
