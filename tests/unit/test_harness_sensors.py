"""Tests for vivify/harness/sensors.py — HarnessSensorEngine."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vivify.config.schema import HarnessConfig
from vivify.harness.sensors import HarnessSensorEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def harness_config():
    """Minimal HarnessConfig with all sensor commands set."""
    return HarnessConfig(
        lint_command="ruff check .",
        typecheck_command="mypy .",
        test_command="pytest",
        build_command="python -m build",
        feedback_timeout_seconds=60,
    )


@pytest.fixture
def engine(harness_config, tmp_path):
    return HarnessSensorEngine(config=harness_config, workspace=tmp_path)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRunAllSensors:
    """Tests for run_all_sensors method."""

    def test_run_all_sensors_all_pass(self, engine):
        """All commands exit code=0 → all_passed=True."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "OK"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            report = engine.run_all_sensors()

        assert report.all_passed is True
        assert report.feedback_prompt == ""
        assert len(report.sensors) == 4

    def test_run_all_sensors_lint_fail(self, engine):
        """lint failure → all_passed=False and feedback_prompt non-empty."""
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            mock_proc = MagicMock()
            if call_count["n"] == 1:  # lint is first
                mock_proc.returncode = 1
                mock_proc.stdout = "error: some lint issue"
                mock_proc.stderr = ""
            else:
                mock_proc.returncode = 0
                mock_proc.stdout = "OK"
                mock_proc.stderr = ""
            return mock_proc

        with patch("subprocess.run", side_effect=side_effect):
            report = engine.run_all_sensors()

        assert report.all_passed is False
        assert report.feedback_prompt != ""

    def test_run_all_sensors_skips_empty_commands(self, tmp_path):
        """Empty commands are skipped; no sensors run for them."""
        config = HarnessConfig(
            lint_command="ruff check .",
            typecheck_command="",
            test_command="",
            build_command="",
            feedback_timeout_seconds=60,
        )
        engine = HarnessSensorEngine(config=config, workspace=tmp_path)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "OK"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            report = engine.run_all_sensors()

        assert len(report.sensors) == 1
        assert report.sensors[0].sensor_type == "lint"


class TestRunSensor:
    """Tests for run_sensor method."""

    def test_run_sensor_timeout(self, engine):
        """Timeout returns exit_code=-1 and TIMEOUT in output."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = engine.run_sensor("test", "pytest", timeout=60)

        assert result.exit_code == -1
        assert result.passed is False
        assert "TIMEOUT" in result.output

    def test_run_sensor_exception(self, engine):
        """Exception captured returns exit_code=-2."""
        with patch("subprocess.run", side_effect=OSError("No such file")):
            result = engine.run_sensor("lint", "ruff check .", timeout=60)

        assert result.exit_code == -2
        assert result.passed is False
        assert "ERROR" in result.output


class TestTruncateOutput:
    """Tests for _truncate_output method."""

    def test_truncate_output_short(self, engine):
        """Short output (under 2000 chars) is not truncated."""
        short = "x" * 100
        assert engine._truncate_output(short) == short

    def test_truncate_output_long(self, engine):
        """Output exceeding 2000 chars is truncated to head+tail."""
        long_output = "A" * 3000
        result = engine._truncate_output(long_output)
        assert len(result) < 3000
        assert "truncated" in result
        # First 500 chars preserved
        assert result.startswith("A" * 500)


class TestGenerateFeedbackPrompt:
    """Tests for generate_feedback_prompt method."""

    def test_generate_feedback_prompt_format(self, engine):
        """Feedback contains markdown heading and code block."""
        from vivify.harness.models import HarnessReport, SensorResult

        report = HarnessReport(
            sensors=[
                SensorResult(
                    sensor_type="lint",
                    passed=False,
                    output="E001: syntax error",
                    duration_seconds=1.0,
                    exit_code=1,
                ),
            ],
            all_passed=False,
        )
        prompt = engine.generate_feedback_prompt(report)
        assert "## Harness Verification Failed" in prompt
        assert "```" in prompt
        assert "Lint" in prompt


class TestIncrementalLint:
    """Tests for _build_incremental_lint_command."""

    def test_incremental_lint_with_files(self, engine):
        """Fewer than 20 files → build incremental lint command."""
        files = ["src/a.py", "src/b.py"]
        result = engine._build_incremental_lint_command("ruff check .", files)
        assert "src/a.py" in result
        assert "src/b.py" in result
        # The trailing "." should be replaced
        assert not result.endswith(" .")

    def test_incremental_lint_too_many_files(self, engine):
        """More than 20 files → fallback to full command."""
        files = [f"file_{i}.py" for i in range(25)]
        result = engine._build_incremental_lint_command("ruff check .", files)
        assert result == "ruff check ."


class TestSensorOrder:
    """Tests for sensor execution order."""

    def test_sensor_order(self, engine):
        """Execution order is lint → typecheck → test → build."""
        order = []

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", "")
            order.append(cmd)
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            return mock_proc

        with patch("subprocess.run", side_effect=side_effect):
            engine.run_all_sensors()

        assert order == ["ruff check .", "mypy .", "pytest", "python -m build"]
