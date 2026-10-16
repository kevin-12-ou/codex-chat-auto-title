import pytest

import rename.namers.cli_namer as cli_namer
from rename.config import Config
from rename.namers import ApiNamer, get_namer


@pytest.fixture(autouse=True)
def isolated_cli_lookup(monkeypatch):
    # Subprocesses are mocked in these tests; npm shims on the developer's
    # actual PATH must not change the argv of a fake invocation.
    monkeypatch.setattr(cli_namer.shutil, "which", lambda name: None)


def _which(*available):
    avail = set(available)
    return lambda name: f"/usr/bin/{name}" if name in avail else None


def test_auto_prefers_claude(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("claude", "codex"))
    assert get_namer(Config(namer="auto")).name == "claude"


def test_auto_falls_back_to_codex(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("codex"))
    assert get_namer(Config(namer="auto")).name == "codex"


def test_auto_falls_back_to_heuristic_without_clis(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which())  # nothing installed
    assert get_namer(Config(namer="auto")).name == "heuristic"


def test_explicit_heuristic_ignores_clis(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("claude"))
    assert get_namer(Config(namer="heuristic")).name == "heuristic"


from rename.models import Message  # noqa: E402

_MSGS = [Message("user", "add CSV export to the reports page and fix pagination")]


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_claude_uses_fast_model_and_clean_output(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw.get("env")
        seen["cwd"] = kw.get("cwd")
        # claude -p prints just the answer text
        return _Proc(stdout="Add CSV export and fix pagination\n")

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("claude", {}).generate(_MSGS)
    assert title == "Add CSV export and fix pagination"
    assert seen["argv"][0] == "claude"
    assert "--model" in seen["argv"] and "haiku" in seen["argv"]
    assert "--no-session-persistence" in seen["argv"]
    assert "--bare" in seen["argv"]
    assert seen["env"]["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] == "1"
    assert seen["cwd"] and seen["cwd"].endswith("namer-scratch")


def test_claude_never_retries_without_ephemeral_flags(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if "--no-session-persistence" in argv or "--bare" in argv:
            return _Proc(returncode=1, stderr="error: unknown option '--no-session-persistence'")
        return _Proc(stdout="Billing export\n")

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("claude", {}).generate(_MSGS)
    assert title is None
    assert len(calls) == 1
    assert "--no-session-persistence" in calls[0]


def test_claude_respects_model_override(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli_namer.subprocess, "run",
        lambda argv, **kw: seen.update(argv=argv) or _Proc(stdout="t\n"),
    )
    cli_namer.CliNamer("claude", {"model": "sonnet"}).generate(_MSGS)
    assert "sonnet" in seen["argv"] and "haiku" not in seen["argv"]


def test_codex_uses_output_last_message_and_default_model(monkeypatch):
    """codex must read its title from --output-last-message, not stdout, and
    default to the working fast Codex model."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["cwd"] = kw.get("cwd")
        # codex streams a noisy transcript to stdout (must be ignored)…
        out_path = argv[argv.index("--output-last-message") + 1]
        # …and writes ONLY the final message to the file:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("CSV Export and Pagination Fix\n")
        return _Proc(stdout="[ts] codex\n[ts] tokens used: 2347\n")

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("codex", {}).generate(_MSGS)
    assert title == "CSV Export and Pagination Fix"  # NOT "tokens used: 2347"
    assert "--output-last-message" in seen["argv"]
    assert "-m" in seen["argv"] and "gpt-5.3-codex-spark" in seen["argv"]
    assert "--ephemeral" in seen["argv"]
    assert "--skip-git-repo-check" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--disable") + 1] == "hooks"
    assert seen["argv"][seen["argv"].index("--sandbox") + 1] == "read-only"
    assert seen["cwd"] and seen["cwd"].endswith("namer-scratch")


def test_codex_never_retries_without_ephemeral_flag(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        out_path = argv[argv.index("--output-last-message") + 1]
        if "--ephemeral" in argv:
            return _Proc(returncode=1, stderr="error: unexpected argument '--ephemeral'")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("Ok title\n")
        return _Proc()

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("codex", {}).generate(_MSGS)
    assert title is None
    assert len(calls) == 1
    assert "--ephemeral" in calls[0]


def test_codex_respects_model_override(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        out_path = argv[argv.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("Custom model title\n")
        return _Proc()

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("codex", {"model": "custom-codex"}).generate(_MSGS)
    assert title == "Custom model title"
    assert "custom-codex" in seen["argv"]
    assert "gpt-5.3-codex-spark" not in seen["argv"]


def test_codex_naming_uses_separate_luna_high_request(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        out_path = argv[argv.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("安装自动命名工具\n")
        return _Proc()

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("codex", {
        "model": "gpt-6-luna", "reasoning_effort": "high", "lightweight": True,
    }).generate(_MSGS)
    assert title == "安装自动命名工具"
    assert seen["argv"][seen["argv"].index("-m") + 1] == "gpt-6-luna"
    assert 'model_reasoning_effort="high"' in seen["argv"]
    assert 'model_reasoning_effort="low"' not in seen["argv"]
    assert "--ephemeral" in seen["argv"]
    assert "resume" not in seen["argv"]


def test_codex_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        cli_namer.subprocess, "run",
        lambda argv, **kw: _Proc(returncode=1, stderr="Unsupported model"),
    )
    assert cli_namer.CliNamer("codex", {"model": "bad"}).generate(_MSGS) is None


def test_windows_npm_shim_uses_node_without_a_shell(tmp_path, monkeypatch):
    shim = tmp_path / "codex.cmd"
    entry = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fixture", encoding="utf-8")
    monkeypatch.setattr(cli_namer.sys, "platform", "win32")
    paths = {"codex": str(shim), "node": str(tmp_path / "node.exe")}
    monkeypatch.setattr(cli_namer.shutil, "which", paths.get)
    seen = {}
    monkeypatch.setattr(
        cli_namer.subprocess, "run",
        lambda argv, **kw: seen.update(argv=argv, kwargs=kw) or _Proc(),
    )
    prompt = '中文 & echo %PATH% $(whoami) "quoted"\nnext line'
    cli_namer._run(["codex", "exec", prompt])
    assert seen["argv"] == [paths["node"], str(entry), "exec", prompt]
    assert not seen["kwargs"].get("shell")
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["creationflags"] & cli_namer.subprocess.CREATE_NO_WINDOW


def test_windows_namer_prefers_native_binary_to_node_chain(tmp_path, monkeypatch):
    shim = tmp_path / "codex.cmd"
    native = (tmp_path / "node_modules/@openai/codex/node_modules/@openai/"
              "codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
    native.parent.mkdir(parents=True)
    native.write_bytes(b"fixture")
    monkeypatch.setattr(cli_namer.sys, "platform", "win32")
    monkeypatch.setattr(cli_namer.shutil, "which", lambda name: str(shim))
    seen = {}
    monkeypatch.setattr(cli_namer.subprocess, "run",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw) or _Proc())
    cli_namer._run(["codex", "--version"])
    assert seen["argv"] == [str(native), "--version"]
    assert seen["kw"]["creationflags"] & cli_namer.subprocess.CREATE_NO_WINDOW


def test_lightweight_codex_disables_context_and_mcp(tmp_path, monkeypatch):
    root = tmp_path / "codex"
    root.mkdir()
    (root / "config.toml").write_text(
        'model_provider="custom"\n[mcp_servers.node_repl]\ncommand="node"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(root))
    argv = cli_namer._lightweight_codex_args()
    assert "mcp_servers.node_repl.enabled=false" in argv
    assert "skills.include_instructions=false" in argv
    assert "project_doc_max_bytes=0" in argv
    assert "model_providers.custom.supports_websockets=false" in argv
    assert "model_providers.custom.request_max_retries=0" in argv
    assert "model_providers.custom.stream_max_retries=0" in argv
    assert "plugins" in argv and "shell_tool" in argv


def test_lightweight_codex_never_forwards_config_secrets(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        '[model_providers.custom]\nexperimental_bearer_token="synthetic-test-secret"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert "synthetic-test-secret" not in " ".join(cli_namer._lightweight_codex_args())


# -- bring-your-own API key (anthropic / openai) ------------------------------ #


def test_api_namer_uses_config_key(monkeypatch):
    """A key in the [anthropic] table is enough — no env var needed."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config(namer="anthropic", raw={"anthropic": {"api_key": "sk-ant-test"}})
    namer = get_namer(cfg)
    assert isinstance(namer, ApiNamer)
    assert namer.name == "anthropic"
    assert namer.available()


def test_api_namer_reads_env_when_no_config_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    namer = get_namer(Config(namer="openai"))
    assert isinstance(namer, ApiNamer)
    assert namer.name == "openai"
    assert namer.available()


def test_api_namer_without_key_falls_back_to_heuristic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get_namer(Config(namer="anthropic")).name == "heuristic"


def test_api_namer_config_model_override(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(namer="openai", raw={"openai": {"api_key": "sk-x", "model": "gpt-4o"}})
    namer = get_namer(cfg)
    assert isinstance(namer, ApiNamer)
    assert namer.model == "gpt-4o"
