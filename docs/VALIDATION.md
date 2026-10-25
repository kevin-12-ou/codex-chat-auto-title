# V2 validation boundaries

Version 0.2.0: **193 tests pass locally**, Ruff passes. CI uses Windows/Python 3.11, 3.13 and 3.14. Automated checks use synthetic local stores.

A real Windows command-shell smoke test completed legacy install with an owned startup shortcut → V2 migration → synchronous UTF-8 Hook delivery → duplicate suppression → one-time claim → disable → enable → uninstall. The old owned shortcut was removed and an unrelated Stop hook preserved. No worker or model request was created in V2; no real chat title was changed.

Tests cover first-turn injection and claim, duplicate/concurrent issue and consumption, UTF-8 process stdout, malformed input, previous-user history, turn change/cancellation/compaction, manual rename and project/cwd/section changes, archived/pinned/subagent/fork/pre-install exclusion, default-title creation races, title format/length, disabled mode, user/project Hook scope, migration/idempotency and no worker start in V2. Native Hook/claim subprocess tests do not call a model or rename real chats.

The local upgrade installs a new synchronous Hook and stops the legacy worker. Hook trust is a human action; real new-chat delivery, native title execution, desktop visibility and title quality require verification after trust. Tests are not evidence of that end-to-end desktop result. No additional model call is needed for the automated checks.

Known limits: native API write is outside the preflight transaction; manual rename can race after claim, model may ignore rules, tool may be unavailable, schemas/context wrappers may change, and internal UI ordering is not controlled. Unclear history fails closed. V2 is not a general cache/quality fix or a guarantee of zero token overhead.

# V1 validation history

Release 0.1.0, Windows development verification:

- 147 automated tests pass locally; Ruff checks pass. GitHub CI runs the same checks across supported Python versions; consult the workflow results for the release commit.

- Automated tests cover queue deduplication, newer-event races, request caps, failure backoff, conservative manual-title detection, affected-row verification, concurrent project/section moves, snapshot rejection and installer merge/idempotency behavior.
- New project/section race and schema-trigger tests failed against the prior adapter, then passed after the guard was added. Existing positive title writes remain covered.
- A consistent copy of a real Codex database containing 226 threads was used for an isolated naming write. Only one title cell changed; all other fields, all other rows and both updated-time/recency query orders stayed identical. No real title was changed by this check.
- The earlier deployed hook/worker implementation completed a real trusted Stop → queue → `gpt-6-luna / high` naming run. Its temporary session was ephemeral and the title target was an isolated fixture. The new project guard uses local regression/real-schema-copy checks; it does not require additional model requests.
- A hidden-process probe returned a zero Windows console-window handle. Official CLI execution succeeded. This supports the hidden-process implementation, not a claim that every desktop popup or performance issue is eliminated.
- Installer tests use isolated homes/configurations. They do not grant Hook trust, modify credentials or change a developer's real hook configuration. Real Windows shortcut generation is checked separately during release preparation; a fresh sign-in and another person's first installation remain manual verification.
- An isolated Windows smoke run completed install → doctor → enable → status → disable → uninstall, preserved an existing unrelated Stop hook and verified and removed the owned startup shortcut. No model requests were made and no real hook configuration was changed.

Limits:

- Actual desktop ordering after independent Codex refresh/resume/sync behavior is not established by SQLite tests. No claim is made that all historic ordering changes have been repaired.
- Desktop project membership may be interpreted from directories and UI state outside this database. The tool preserves database project/section/cwd fields; it does not control every UI layer.
- Internal Codex schemas and CLI flags may change. Failures retain the original title; inspect compatibility after upgrades.
- A per-row postcondition cannot detect a hypothetical future trigger that changes a different row/table. Current inspected Codex thread triggers do not add such a title side effect.
- Model availability and title quality vary. The default model worked in the tested environment, not necessarily every account.
- No automated test sends paid model requests. Cross-platform service support is not claimed.
