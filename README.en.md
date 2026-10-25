# Codex Chat Auto Title v2

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md)

First-message automatic chat titles for Codex on Windows. A synchronous **UserPromptSubmit hook** injects one short instruction into a genuinely new first turn. The current model completes the user's task, checks a local one-time permission, then calls the desktop **set_thread_title** tool before its final response. No explanation about naming is added to the reply.

Format: `module|ongoing goal`, at most **24 Unicode code points including the separator**. Prefer understandable business/function names and the outcome of the whole conversation, not the current step or vague goals such as “optimize”. Example: `Login|Ship WeChat auth`.

## Install and trust

Requires Windows, Python 3.11+ and a Codex environment supporting Hooks and the desktop `set_thread_title` tool. Other clients may lack this tool and will skip naming. Use the complete source ZIP/checkout, not a wheel alone.

```powershell
python -X utf8 manage.py install
python -X utf8 manage.py doctor
```

Default scope is the user's `CODEX_HOME/hooks.json`. For project-local scope, use `install --project-root 'D:\Projects\my-project'`. One installation manages one scope; uninstall before changing scope.

Open **Codex > Settings > Hooks**, refresh, review **Codex Chat Auto Title v2: first message only**, verify its `python.exe -X utf8 .../first_prompt.py` command and trust it. Reopen the project/application if discovery needs a reload. Trust is never granted automatically. Start a new chat to test; existing chats are excluded.

## Behavior and boundaries

- No background worker, startup task, subagent, model switch or separate naming-model request in default V2 mode. Normal context/tool token overhead still applies.
- Uses installation time, canonical local metadata and bounded transcript inspection to exclude historical, forked, subagent, pinned, archived and ambiguous threads. First seen is not first message.
- Only recognized default titles qualify. A preflight check skips manual renames and project/section moves made during the task. Manual titles identical to defaults cannot be distinguished.
- SQLite deduplication issues and consumes at most one permission. Follow-up turns, cancellation and compaction expire it. No automatic retry or alternative write path.
- The native tool changes the current title; no move/project tool or global sidebar sort setting is used. The native API is outside the local transaction: a narrow manual-rename race remains between preflight and API execution. Model compliance and UI ordering are not guaranteed by fixture tests.
- No new history scan, no historical backfill, no timestamp restoration. Missing/incompatible history, missing tool or model noncompliance retains the original title.

`runtime/` contains private title snapshots, IDs, paths and permissions. Do not upload it. Prompt candidates are stored as hashes, not full prompt text; snapshots may contain the original title. V2 adds rules to the current conversation and sends no separate transcript excerpt. No extra API key or runtime dependencies.

## Upgrade, control and legacy mode

Keep private `runtime/`, replace source in the existing installation and run `manage.py install`. The installer stops its owned V1 worker, removes its registered old hook/startup and installs V2. Review and trust the new hook. Other installations/third-party hooks are not removed automatically.

```powershell
python -X utf8 manage.py status
python -X utf8 manage.py disable
python -X utf8 manage.py enable
python -X utf8 manage.py uninstall
```

V2 enable/start does not spawn a worker. Disable blocks unclaimed permissions; an already-approved native tool call cannot be revoked. Uninstall preserves titles and runtime. Do not delete one-time state.

Optional [legacy Stop-hook worker](docs/LEGACY.md):

```powershell
python -X utf8 manage.py install --mode hook_queue --model gpt-6-luna --reasoning-effort high --max-calls-per-day 30 --startup --start
```

Only legacy mode uses the separate model, daily cap, excerpt sending and transactional SQLite title writer. V2 uses the active model and does not use that daily counter.

## Development

```powershell
python -m pip install -e '.[dev]'
python -X utf8 -m pytest -q
python -X utf8 -m ruff check .
```

Independent community project, not an OpenAI product. See [official Hooks documentation](https://learn.chatgpt.com/docs/hooks). MIT; includes code derived from [study8677/rename](https://github.com/study8677/rename), credited in NOTICE. Inherited generic rename CLI is an internal library interface, not the supported installation entrypoint.
