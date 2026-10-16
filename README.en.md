# Codex Chat Auto Title

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [AI-readable overview](llms.txt)

Quiet automatic chat renaming for **Codex on Windows**. An asynchronous **Stop hook** enqueues a thread ID. A lightweight **Python worker** waits for idle time, summarizes a short excerpt using your existing Codex CLI login, and changes only the display title.

Independent community tool, not an OpenAI product. It writes Codex's internal local SQLite store. Tested with Codex CLI **0.154.0**, Windows and Python 3.14. Python 3.11+ is required. macOS/Linux service installation is not implemented.

## Why

- Short searchable titles in the conversation's language, instead of long first-message titles.
- No startup scan of old history. Only a new completed reply can enqueue a chat.
- Hidden Windows processes; no visible terminal required for background naming.
- Conservative default-title matching: manual/uncertain titles, pinned, archived and running chats are skipped.
- Projects, directories, sections, pinning and ordering timestamps are preserved. Concurrent moves cancel the write. A transaction checks all other fields of the affected row before commit.
- Default: **30 naming attempts per local day across all chats**, 10 minutes idle, at least 5 minutes between calls. Failures count. No timer-driven retries for a failed event.
- Separate ephemeral CLI invocation; the current chat's model, context and global Codex configuration are unchanged. Hooks are disabled inside the naming invocation to prevent recursion.

## Install

Requires Windows, Python 3.11+ including `pythonw.exe`, Codex with Hooks, and an installed/logged-in Codex CLI available as `codex` on PATH. CLI and desktop must use the same intended Codex home/account.

Download and extract the [latest release](https://github.com/kevin-12-ou/codex-chat-auto-title/releases/latest) into a permanent directory. Review the code, then run in PowerShell:

```powershell
python -X utf8 manage.py install --model gpt-6-luna --reasoning-effort high --max-calls-per-day 30 --startup --start
```

Choose a model actually available to your account. `gpt-6-luna / high` is the deployment default, not a promise of availability for every account. No fallback to another model occurs on failure.

`--startup` opts into Windows sign-in startup; `--start` starts immediately. Existing hooks are merged and backed up. Reinstallation is idempotent and preserves your existing disabled setting.

Open **Codex → Settings → Hooks**, refresh, review **Codex Chat Auto Title: enqueue only**, and trust it yourself. The installer never bypasses trust. See the [official Hooks documentation](https://learn.chatgpt.com/docs/hooks). Keep the installation directory in place. Disable other auto-title tools before installing this one.

## Usage

```powershell
python -X utf8 manage.py doctor
python -X utf8 manage.py status
python -X utf8 manage.py disable
python -X utf8 manage.py enable
python -X utf8 manage.py uninstall
```

Normal completed replies enqueue chats automatically. `doctor` and `status` do not call a model. `disable` persists across restarts; `stop` only stops the current worker. Uninstall removes this installation's exact hook and owned startup shortcut, retaining runtime data and existing titles. An already dispatched request may still finish after stopping; the stopped worker cannot commit its result.

Edit `runtime/background-settings.json` to adjust `model`, `reasoning_effort`, `max_calls_per_day`, `idle_seconds`, `min_call_interval_seconds`, `failure_backoff_seconds`, `poll_seconds` or `enabled`. The worker reloads it every minute. The limit is a maximum, not a daily target or a currency/token budget. It covers this tool, not your other Codex activity.

## Privacy and cost

There are no third-party runtime dependencies or extra API keys. **Naming uses your existing Codex/configured provider allowance.** The worker reads the target transcript locally and sends a shortened excerpt of up to approximately 3,200 characters to the service configured in your Codex CLI. Do not enable it for data you cannot send there.

The hook itself only records thread/turn IDs. `runtime/` contains private titles, paths and IDs; never publish it. Losing runtime state loses idempotency, manual-title locks, daily counters and restore history.

## Projects and ordering

Only `name` (paginated history) or `title` (legacy history) is updated. Project ID, cwd, sections, pinning, ordinary update timestamps and separate recency timestamps stay unchanged. Schema-trigger changes to other fields in that row cause rollback.

This protects this tool's writes. Codex may independently refresh order when opening, resuming, syncing or migrating history. We do not force historical timestamps backward or change sidebar preferences. The database also lacks a reliable title-author flag: manually setting exactly the default text is indistinguishable from a default title.

## Restore

```powershell
python -X utf8 manage.py disable
python -X utf8 background.py restore
python -X utf8 background.py restore --apply
```

Preview first. Only successfully applied journal entries whose current title still matches the tool's last title can be restored. Later manual edits are preserved. No old database is copied over live history. Review compatibility before restoring after a Codex upgrade.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests background.py hook_queue.py manage.py
```

Use the complete source checkout/ZIP; this project is not published on PyPI. `src/rename` includes inherited internal library code; the older general-purpose scanning CLI is outside this project's supported workflow and limits. See [validation boundaries](docs/VALIDATION.md).

MIT, derived in part from [study8677/rename](https://github.com/study8677/rename), commit `abfb0151942bd0fbea32e10e5d1e6ae3fb7640f1`. Original copyright retained. See [NOTICE](NOTICE).
