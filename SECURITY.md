# Security and privacy

This program reads target Codex chat history, sends shortened excerpts through the user's existing Codex CLI configuration, and writes local display titles. It is not a sandbox for confidential transcripts and does not replace Codex's own permissions or provider data policies.

Do not include credentials, auth.json, config.toml, SQLite stores, rollout files, title journals, runtime directories or private screenshots in bug reports. Tests use synthetic credentials and isolated homes. Runtime is excluded from Git and release archives.

Hooks must be explicitly reviewed and trusted in Codex. The installer does not write trust state. The hook accepts a UUID and bounded turn ID, never opens input-supplied paths, and never executes transcript instructions. Model output is shaped as text and passed to parameterized SQL, never executed as code.

For a sensitive issue, use GitHub's private vulnerability reporting feature if enabled. Otherwise contact the repository maintainer privately through their public profile before publishing details. Do not open a public issue containing secrets.
