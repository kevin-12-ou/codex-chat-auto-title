"""Namer that shells out to an installed CLI (`claude` or `codex`).

Reuses whatever login the user already has for that tool — no API key wiring,
no extra cost beyond the tool's own usage. This is the default (via ``auto``),
which prefers ``claude`` then ``codex``.

Calls are ephemeral: they must not land in the user's Claude Code / Codex
session list. A persisted namer call shows up as a real session titled with
the naming prompt itself, and rename would then try to rename *that* session
by calling the CLI again — a loop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from .. import util
from .base import INSTRUCTION, Namer, build_excerpt

_TIMEOUT = 90  # codex with reasoning can take a while; keep generous

# Official Codex CLI model id (OpenAI, Feb 2026). Fast enough for a 6-word title.
_CODEX_DEFAULT_MODEL = "gpt-5.3-codex-spark"
# Claude's small/fast model — plenty for a 6-word title, and cheap.
_CLAUDE_DEFAULT_MODEL = "haiku"

# Flags that stop the namer call from showing up as a real coding session.
# Never drop these flags on older CLIs: that would persist naming sessions
# and reintroduce the feedback loop this code is supposed to prevent.
_CLAUDE_EPHEMERAL_FLAGS = ("--bare", "--no-session-persistence")
_CODEX_EPHEMERAL_FLAGS = ("--ephemeral", "--skip-git-repo-check")


def _lightweight_codex_args() -> list[str]:
    """Opt-in metadata-only namer context, retaining the user's CLI login."""
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = root / "config.toml"
    cfg = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
    argv = []
    for feature in ("plugins", "memories", "shell_tool", "skill_search"):
        argv += ["--disable", feature]
    for name in cfg.get("mcp_servers", {}):
        # CLI override paths are split on dots, rather than parsed as quoted
        # TOML keys. Refuse ambiguous names instead of accidentally enabling MCP.
        if not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError("Cannot disable an MCP name containing special path characters")
        argv += ["-c", f"mcp_servers.{name}.enabled=false"]
    for override in (
        "project_doc_max_bytes=0",
        "skills.include_instructions=false",
        "skills.bundled.enabled=false",
        "features.skip_host_skill_discovery=true",
    ):
        argv += ["-c", override]
    instructions = Path(__file__).with_name("title_instructions.txt").resolve()
    # The value, unlike the key path, is TOML parsed. JSON strings are valid TOML strings.
    import json

    argv += ["-c", "model_instructions_file=" + json.dumps(str(instructions))]
    provider = cfg.get("model_provider", "openai")
    if provider != "openai":
        for option in (
            "supports_websockets=false", "request_max_retries=0", "stream_max_retries=0",
        ):
            argv += ["-c", f"model_providers.{provider}.{option}"]
    return argv


def _scratch_cwd() -> str:
    """Isolated cwd so a leaking CLI cannot dump sessions into a user project."""
    path = util.state_dir() / "namer-scratch"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _unknown_flag(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(
        needle in low
        for needle in (
            "unknown option",
            "unknown argument",
            "unexpected argument",
            "unrecognized",
            "invalid option",
            "unexpected option",
        )
    )


def _run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        # Windows CreateProcess does not resolve npm's .cmd shims like a
        # shell does. Launch the known JS entry with node directly; keep the
        # conversation as an argv item, never interpolate it into cmd.exe.
        resolved = shutil.which(argv[0]) if sys.platform == "win32" else None
        if resolved and Path(resolved).suffix.lower() in (".cmd", ".bat"):
            if argv[0] == "codex":
                # Avoid Node spawning a second console process. These are the
                # same vendor layouts resolved by the installed official shim.
                package = Path(resolved).parent / "node_modules/@openai/codex"
                for arch, triple in (
                    ("x64", "x86_64-pc-windows-msvc"),
                    ("arm64", "aarch64-pc-windows-msvc"),
                ):
                    roots = (
                        package / f"node_modules/@openai/codex-win32-{arch}/vendor",
                        package.parent / f"codex-win32-{arch}/vendor",
                        package / "vendor",
                    )
                    native = next((root / triple / "bin/codex.exe" for root in roots
                                   if (root / triple / "bin/codex.exe").is_file()), None)
                    if native:
                        argv = [str(native), *argv[1:]]
                        break
            entries = {
                "codex": "@openai/codex/bin/codex.js",
                "claude": "@anthropic-ai/claude-code/cli.js",
            }
            if argv[0] in entries:
                entry = Path(resolved).parent / "node_modules" / entries[argv[0]]
                node = shutil.which("node")
                if not entry.is_file() or not node:
                    raise OSError("npm CLI entry or node is missing; install the native CLI")
                argv = [node, str(entry), *argv[1:]]
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            env=merged,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        util.log(f"{argv[0]} namer call failed: {exc}", level="debug")
        return None


def _run_ephemeral(
    required: list[str],
    optional: tuple[str, ...],
    trailing: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
):
    """Fail closed if the CLI cannot keep this naming call ephemeral."""
    proc = _run(required + list(optional) + trailing, env=env, cwd=cwd)
    if proc is not None and proc.returncode != 0 and _unknown_flag(proc.stderr):
        util.log(
            f"{required[0]} cannot run the ephemeral namer; upgrade the CLI. "
            "Not retrying with session persistence enabled.",
            level="warn",
        )
    return proc


class CliNamer(Namer):
    def __init__(self, name: str, options: dict | None = None):
        self.name = name  # "claude" or "codex"
        self.options = options or {}

    def available(self) -> bool:
        return shutil.which(self.name) is not None

    def _prompt(self, messages) -> str | None:
        excerpt = build_excerpt(messages)
        if not excerpt:
            return None
        return f"{INSTRUCTION}\n\n--- conversation ---\n{excerpt}\n--- end ---"

    # -- claude ------------------------------------------------------------ #
    def _generate_claude(self, prompt: str) -> str | None:
        argv = ["claude"]
        model = self.options.get("model", _CLAUDE_DEFAULT_MODEL)
        if model:
            argv += ["--model", str(model)]
        env = {"CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1"}
        proc = _run_ephemeral(
            argv,
            _CLAUDE_EPHEMERAL_FLAGS,
            ["-p", prompt],
            env=env,
            cwd=_scratch_cwd(),
        )
        if proc is None:
            return None
        if proc.returncode != 0:
            util.log(
                f"claude namer exited {proc.returncode}: {proc.stderr.strip()[:160]}",
                level="debug",
            )
            return None
        # `claude -p` prints just the response; take the last non-empty line.
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else None

    # -- codex ------------------------------------------------------------- #
    def _generate_codex(self, prompt: str) -> str | None:
        # `codex exec` streams a noisy transcript to stdout, so we ask it to
        # write ONLY the final assistant message to a file and read that back.
        fd, out_path = tempfile.mkstemp(prefix="rename-codex-", suffix=".txt")
        os.close(fd)
        try:
            argv = ["codex", "exec", "--disable", "hooks", "--sandbox", "read-only"]
            env = None
            if self.options.get("lightweight", False):
                argv += _lightweight_codex_args()
                if sys.platform == "win32":
                    env = {
                        key.upper() + "_PROXY": value
                        for key, value in urllib.request.getproxies().items()
                        if key in ("http", "https")
                    }
            model = self.options.get("model", _CODEX_DEFAULT_MODEL)
            if model:
                argv += ["-m", str(model)]
            effort = self.options.get(
                "reasoning_effort", "low" if self.options.get("lightweight") else None
            )
            if effort is not None:
                if effort not in (
                    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
                ):
                    raise ValueError("Unsupported reasoning effort")
                argv += ["-c", f'model_reasoning_effort="{effort}"']
            proc = _run_ephemeral(
                argv,
                _CODEX_EPHEMERAL_FLAGS,
                ["--output-last-message", out_path, prompt],
                cwd=_scratch_cwd(),
                env=env,
            )
            if proc is None:
                return None
            if proc.returncode != 0:
                util.log(
                    f"codex namer exited {proc.returncode}: "
                    f"{proc.stderr.strip()[:160]}",
                    level="debug",
                )
                return None
            try:
                text = open(out_path, encoding="utf-8", errors="replace").read()
            except OSError:
                return None
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return lines[-1] if lines else None
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def generate(self, messages, *, old_title=None, cwd=None, tool=None):
        prompt = self._prompt(messages)
        if not prompt:
            return None
        if self.name == "codex":
            return self._generate_codex(prompt)
        return self._generate_claude(prompt)
