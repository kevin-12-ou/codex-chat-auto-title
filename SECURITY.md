# Security and privacy

V2 reads the current local thread's metadata and a canonical transcript bounded to 2 MiB. The synchronous UserPromptSubmit handler emits fixed naming rules, identifiers and a local guard command into the current conversation. It runs no model process, sends no separate excerpt and does not execute transcript content. Candidate titles are hashed; original-title snapshots, IDs and paths remain private in runtime.

The current model is asked to call native set_thread_title after a one-time local preflight. This is a cooperative instruction, not an atomic native compare-and-set or a model-compliance sandbox. A narrow race remains between checking and native renaming. Explicit user choices take precedence; missing tools or uncertain metadata should skip silently. Do not rely on auto-title as a security boundary.

Optional legacy mode sends shortened excerpts through the configured Codex CLI/provider and writes title fields in guarded local SQLite transactions. It shares your configured account allowance. See docs/LEGACY.md.

Hooks must be reviewed and trusted by the human. Installation never writes trust state. Installation paths reject shell metacharacters. Hook-supplied paths are not opened. Use isolated synthetic stores for tests.

Never publish credentials, auth.json, config.toml, databases, rollout files, title journals, runtime or private screenshots. Runtime and private QA artifacts are excluded from release archives. For sensitive bugs, use private vulnerability reporting if enabled or contact the maintainer via their public profile; do not post secrets in public issues.
