# V2 architecture

Default mode: `first_prompt`; optional V1 mode: `hook_queue`.

```text
UserPromptSubmit -> Python (synchronous, UTF-8)
  -> enabled? UUID IDs? root agent? new since install?
  -> read only current thread row and canonical bounded transcript
  -> reject old/forked/ambiguous/manual/pinned/archived threads
  -> SQLite INSERT OR IGNORE: one first-turn ticket
  -> JSON additionalContext: finish task, then name once
current model finishes first task
  -> first_prompt.py --claim THREAD TURN --title TITLE
  -> validate <=24 code points, one |, meaningful goal shape
  -> transaction: consume ticket, verify current title/project/section/turn
  -> allowed=true -> native set_thread_title for current chat
follow-ups -> no additionalContext, no extra naming process
```

The hook is synchronous so rules are available before the first model request. `python.exe` supplies redirected UTF-8 stdout (pythonw has no streams). The installed Codex host is responsible for hiding command-hook consoles. No worker or model subprocess is launched in V2.

A preflight reads Codex's highest-version local state SQLite, then opens only the canonical rollout path contained within `CODEX_HOME/sessions`. Never open payload-supplied paths. Reads are capped at 2 MiB; malformed, compacted or unrecognized history fails closed. Metadata must identify the same thread, all persisted turn markers must identify the first current turn, and prior actual user/assistant history causes submission rejection. Claiming may include first-turn assistant activity. Reserved environment/AGENTS/page context wrappers are excluded from user-message comparison. Unknown wrappers can cause safe false negatives.

Local `runtime/first-prompt.sqlite` stores IDs, status, canonical DB path, protected-field snapshot and candidate-title hashes. It stores no full prompt text. Snapshot includes original display title and may be sensitive. Unique thread key and immediate claim transaction handle duplicates/concurrent invocation. Claim checks enabled mode, title shape and first-turn identity, then consumes before native tool use. Failure never starts a fallback writer or model. Later events do not reissue instructions.

The model receives only the fixed rules, IDs and local guard command. It must run the guard before the native tool, omit threadId to target this chat, skip missing tool and never explain naming. This is a cooperative model workflow, not an enforcement sandbox. Model noncompliance is possible. Check-to-native-call is not atomic; a manual rename in that gap can race. Native API can have client-specific ordering behavior. V2 cannot claim V1's transaction-wide all-other-fields postcondition around a desktop API call.

Install merges only exact owned command hooks, backs up hooks.json, preserves unrelated handlers and supports a single user/project scope. Trust is always reviewed in Codex. Legacy worker exits immediately if V2 mode is active, including if an obsolete startup shortcut invokes it.

## Legacy mode

See [legacy worker architecture](LEGACY-ARCHITECTURE.md). `background.py` and `hook_queue.py` remain for explicit `--mode hook_queue`; their title-only transactional protection and daily budget remain available. V2 never writes Codex title fields directly. `background.py restore` applies only to the legacy journal, not native V2 renames.
