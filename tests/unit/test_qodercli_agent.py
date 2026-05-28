"""Tests for ``vivify.agents.qodercli_agent`` — mocks subprocess."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivify.agents.qodercli_agent import (
    QoderCliAgent,
    QoderCliConfig,
    _filter_hooks,
)
from vivify.agents.slot_manager import AGENT_ENV_TAG


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "wt"
    ws.mkdir()
    return ws


def _make_agent() -> QoderCliAgent:
    cfg = QoderCliConfig(
        binary_path="/usr/local/bin/qodercli",
        model="ultimate",
        max_turns_default=30,
        timeout_seconds_default=120,
        extra_args=("--yolo", "-q"),
        max_concurrent_processes=0,  # disable slot waiting in unit tests
        auto_trust_workspace=True,
    )
    return QoderCliAgent(cfg)


def test_filter_hooks_strips_hook_timing_lines():
    raw = "first line\n[hook timing] something 12ms\nsecond line\n"
    assert _filter_hooks(raw) == "first line\nsecond line"


def test_filter_hooks_handles_empty():
    assert _filter_hooks("") == ""
    assert _filter_hooks(None) == ""  # type: ignore[arg-type]


def test_heal_command_construction(workspace):
    agent = _make_agent()
    fake_complete = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True), \
         patch("subprocess.run", return_value=fake_complete) as mocked:
        result = agent.heal(
            "do the thing",
            max_turns=42,
            category="test_cat",
            workspace=workspace,
        )

    assert result.success is True
    assert result.exit_code == 0

    args, kwargs = mocked.call_args
    cmd = args[0]
    assert cmd[0] == "/usr/local/bin/qodercli"
    assert cmd[1] == "-p"
    assert cmd[2] == "do the thing"
    assert "--yolo" in cmd
    assert "-q" in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "ultimate"
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "42"
    assert "-w" in cmd and cmd[cmd.index("-w") + 1] == str(workspace)

    env = kwargs["env"]
    assert env[AGENT_ENV_TAG] == "1"
    assert env["TERM"] == "dumb"

    assert kwargs["input"] == "yes\n"
    assert kwargs["cwd"] == str(workspace)


def test_heal_returns_failure_when_binary_missing(workspace):
    agent = QoderCliAgent(QoderCliConfig(binary_path="this-bin-does-not-exist"))
    with patch("shutil.which", return_value=None):
        result = agent.heal(
            "prompt", max_turns=10, category="cat", workspace=workspace,
        )
    assert result.success is False
    assert result.exit_code == -1
    assert "binary not found" in (result.error or "")


def test_heal_returns_failure_when_workspace_missing(tmp_path):
    agent = _make_agent()
    missing = tmp_path / "does-not-exist"
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True):
        result = agent.heal(
            "prompt", max_turns=5, category="cat", workspace=missing,
        )
    assert result.success is False
    assert "workspace does not exist" in (result.error or "")


def test_heal_handles_nonzero_exit(workspace):
    agent = _make_agent()
    fake_complete = MagicMock(returncode=2, stdout="", stderr="boom\n")
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True), \
         patch("subprocess.run", return_value=fake_complete):
        result = agent.heal("p", max_turns=5, category="c", workspace=workspace)
    assert result.success is False
    assert result.exit_code == 2
    assert "boom" in (result.error or "")


def test_heal_handles_timeout(workspace):
    agent = _make_agent()
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True), \
         patch(
             "subprocess.run",
             side_effect=subprocess.TimeoutExpired(cmd="qodercli", timeout=120),
         ):
        result = agent.heal("p", max_turns=5, category="c", workspace=workspace)
    assert result.success is False
    assert result.exit_code == -1
    assert "timed out" in (result.error or "")


def test_heal_filters_hooks_in_output(workspace):
    agent = _make_agent()
    fake = MagicMock(
        returncode=0,
        stdout="real out\n[hook timing] post 1ms\nmore\n",
        stderr="",
    )
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True), \
         patch("subprocess.run", return_value=fake):
        result = agent.heal("p", max_turns=5, category="c", workspace=workspace)
    assert "[hook timing]" not in result.output
    assert "real out" in result.output
    assert "more" in result.output


def test_extra_env_merged(workspace):
    agent = _make_agent()
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("os.path.isfile", return_value=True), \
         patch("os.access", return_value=True), \
         patch("subprocess.run", return_value=fake) as mocked:
        agent.heal(
            "p", max_turns=5, category="c", workspace=workspace,
            env={"GH_TOKEN": "ghp_test"},
        )
    env = mocked.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == "ghp_test"
    assert env[AGENT_ENV_TAG] == "1"


def test_agent_name():
    assert _make_agent().name() == "qodercli"


# ── Differentiated parameter injection (Task #87) ────────────────────────────


def _build_cfg(**overrides) -> QoderCliConfig:
    base = dict(
        binary_path="/usr/local/bin/qodercli",
        model="ultimate",
        max_turns_default=30,
        timeout_seconds_default=120,
        extra_args=("--yolo", "-q"),
        max_concurrent_processes=0,
        auto_trust_workspace=True,
        max_attachments=0,  # disable by default; individual tests can opt in
    )
    base.update(overrides)
    return QoderCliConfig(**base)


def _make_agent_with(cfg: QoderCliConfig) -> QoderCliAgent:
    """Build agent and stub knowledge augmentation to keep prompts deterministic."""
    agent = QoderCliAgent(cfg)
    agent._augment_prompt_with_knowledge = (  # type: ignore[assignment]
        lambda prompt, workspace, **kwargs: prompt
    )
    return agent


class TestBuildCmd:
    """Tests for QoderCliAgent._build_cmd() conditional parameter injection."""

    def test_reasoning_effort_injection(self, workspace):
        cfg = _build_cfg(
            reasoning_effort_by_category={
                "fix_issue": "high",
                "develop_feature": "medium",
            },
        )
        agent = _make_agent_with(cfg)

        cmd_fix = agent._build_cmd(
            "prompt", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert "--reasoning-effort" in cmd_fix
        assert cmd_fix[cmd_fix.index("--reasoning-effort") + 1] == "high"

        cmd_dev = agent._build_cmd(
            "prompt", workspace=workspace, category="develop_feature", max_turns=5,
        )
        assert cmd_dev[cmd_dev.index("--reasoning-effort") + 1] == "medium"

    def test_reasoning_effort_not_injected_when_missing(self, workspace):
        cfg = _build_cfg(reasoning_effort_by_category={"fix_issue": "high"})
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "prompt", workspace=workspace, category="goal_decompose", max_turns=5,
        )
        assert "--reasoning-effort" not in cmd

    def test_system_prompt_suffix_injection(self, workspace):
        cfg = _build_cfg(system_prompt_suffix="Be careful with edge cases.")
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Be careful with edge cases."

    def test_system_prompt_suffix_empty_not_injected(self, workspace):
        cfg = _build_cfg(system_prompt_suffix="")
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert "--append-system-prompt" not in cmd

    def test_max_output_tokens_injection(self, workspace):
        cfg = _build_cfg(
            max_output_tokens_by_category={
                "fix_issue": 4096,
                "develop_feature": 8192,
            },
        )
        agent = _make_agent_with(cfg)

        cmd_fix = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert cmd_fix[cmd_fix.index("--max-output-tokens") + 1] == "4096"

        cmd_dev = agent._build_cmd(
            "p", workspace=workspace, category="develop_feature", max_turns=5,
        )
        assert cmd_dev[cmd_dev.index("--max-output-tokens") + 1] == "8192"

        # Category not present in map → no injection
        cmd_other = agent._build_cmd(
            "p", workspace=workspace, category="goal_decompose", max_turns=5,
        )
        assert "--max-output-tokens" not in cmd_other

    def test_agent_selection_by_category(self, workspace):
        cfg = _build_cfg(
            agent_for_category={
                "goal_decompose": "Plan",
                "develop_feature": "Coder",
            },
        )
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="goal_decompose", max_turns=5,
        )
        assert "--agent" in cmd
        assert cmd[cmd.index("--agent") + 1] == "Plan"

    def test_agent_not_injected_when_category_missing(self, workspace):
        cfg = _build_cfg(agent_for_category={"goal_decompose": "Plan"})
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert "--agent" not in cmd

    def test_explicit_agent_name_overrides_category(self, workspace):
        cfg = _build_cfg(agent_for_category={"goal_decompose": "Plan"})
        agent = _make_agent_with(cfg)
        cmd = agent._build_cmd(
            "p",
            workspace=workspace,
            category="goal_decompose",
            max_turns=5,
            agent_name="Coder",
        )
        assert cmd[cmd.index("--agent") + 1] == "Coder"
        # Plan must not appear as an agent value
        assert "Plan" not in cmd

    def test_attachment_injection(self, workspace):
        cfg = _build_cfg(max_attachments=3)
        agent = _make_agent_with(cfg)
        fake_files = [
            workspace / "a.py",
            workspace / "b.py",
        ]
        agent._get_attachments = (  # type: ignore[assignment]
            lambda prompt, ws: list(fake_files)
        )
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        # Each attachment should appear as a separate --attachment flag.
        attach_indexes = [i for i, v in enumerate(cmd) if v == "--attachment"]
        assert len(attach_indexes) == 2
        injected_paths = [cmd[i + 1] for i in attach_indexes]
        assert injected_paths == [str(p) for p in fake_files]

    def test_attachment_max_limit(self, workspace):
        cfg = _build_cfg(max_attachments=2)
        agent = _make_agent_with(cfg)
        fake_files = [
            workspace / "a.py",
            workspace / "b.py",
            workspace / "c.py",
            workspace / "d.py",
        ]
        agent._get_attachments = (  # type: ignore[assignment]
            lambda prompt, ws: list(fake_files)
        )
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        attach_indexes = [i for i, v in enumerate(cmd) if v == "--attachment"]
        # Capped at 2 by max_attachments.
        assert len(attach_indexes) == 2

    def test_attachment_disabled_when_max_zero(self, workspace):
        cfg = _build_cfg(max_attachments=0)
        agent = _make_agent_with(cfg)
        called = {"n": 0}

        def _fake(prompt, ws):
            called["n"] += 1
            return [workspace / "a.py"]

        agent._get_attachments = _fake  # type: ignore[assignment]
        cmd = agent._build_cmd(
            "p", workspace=workspace, category="fix_issue", max_turns=5,
        )
        assert "--attachment" not in cmd
        # When max_attachments is 0 the helper should not even be invoked.
        assert called["n"] == 0

    def test_backward_compatibility_no_category(self, workspace):
        """heal() defaults category='fix_issue'; ensure no injection breaks the cmd."""
        cfg = _build_cfg()
        agent = _make_agent_with(cfg)
        # Default heal-style invocation: no per-category configs at all.
        cmd = agent._build_cmd(
            "hello", workspace=workspace, category="fix_issue", max_turns=10,
        )
        assert cmd[0] == "/usr/local/bin/qodercli"
        assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "hello"
        assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "ultimate"
        assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "10"
        # No optional flags injected.
        assert "--reasoning-effort" not in cmd
        assert "--append-system-prompt" not in cmd
        assert "--max-output-tokens" not in cmd
        assert "--agent" not in cmd
        assert "--attachment" not in cmd


class TestGetAttachments:
    """Tests for QoderCliAgent._get_attachments()."""

    def test_returns_empty_when_no_knowledge_graph(self, workspace, monkeypatch):
        """Provider with graph=None ⇒ empty attachments."""
        agent = _make_agent_with(_build_cfg(max_attachments=3))

        class FakeProvider:
            def __init__(self, root):
                self.graph = None

            def recommend_files(self, *a, **kw):  # pragma: no cover - not called
                return [Path("/should/not/be/called")]

        from vivify.knowledge import context_provider as cp

        monkeypatch.setattr(cp, "KnowledgeContextProvider", FakeProvider)
        assert agent._get_attachments("prompt", workspace) == []

    def test_returns_empty_on_import_error(self, workspace, monkeypatch):
        """Importing the provider blowing up ⇒ silent empty list."""
        import builtins

        agent = _make_agent_with(_build_cfg(max_attachments=3))
        real_import = builtins.__import__

        def boom(name, *a, **kw):
            if name == "vivify.knowledge.context_provider":
                raise ImportError("forced")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", boom)
        assert agent._get_attachments("prompt", workspace) == []

    def test_returns_recommended_files(self, workspace, monkeypatch):
        """Happy path: provider returns recommended files; agent passes them through."""
        agent = _make_agent_with(_build_cfg(max_attachments=2))
        target = [workspace / "x.py", workspace / "y.py"]

        class FakeProvider:
            def __init__(self, root):
                self.graph = object()  # truthy

            def recommend_files(self, query, ws, max_files=3):
                assert max_files == 2
                return list(target)

        from vivify.knowledge import context_provider as cp

        monkeypatch.setattr(cp, "KnowledgeContextProvider", FakeProvider)
        out = agent._get_attachments("some prompt", workspace)
        assert out == target

    def test_returns_empty_when_recommend_raises(self, workspace, monkeypatch):
        agent = _make_agent_with(_build_cfg(max_attachments=2))

        class FakeProvider:
            def __init__(self, root):
                self.graph = object()

            def recommend_files(self, *a, **kw):
                raise RuntimeError("db corrupt")

        from vivify.knowledge import context_provider as cp

        monkeypatch.setattr(cp, "KnowledgeContextProvider", FakeProvider)
        assert agent._get_attachments("prompt", workspace) == []
