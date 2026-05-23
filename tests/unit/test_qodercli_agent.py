"""Tests for ``auto_heal.agents.qodercli_agent`` — mocks subprocess."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auto_heal.agents.qodercli_agent import (
    QoderCliAgent,
    QoderCliConfig,
    _filter_hooks,
)
from auto_heal.agents.slot_manager import AGENT_ENV_TAG


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
