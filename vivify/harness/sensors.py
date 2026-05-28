"""Harness sensor engine for post-fix verification."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from vivify.config.schema import HarnessConfig
from vivify.harness.models import HarnessReport, SensorResult

logger = logging.getLogger(__name__)


class HarnessSensorEngine:
    """Execute project verification sensors and collect results.

    The engine runs configured sensors (lint/typecheck/test/build) in a fixed
    priority order after AI-driven changes, aggregates the outcomes into a
    :class:`HarnessReport`, and produces a feedback prompt that can be
    re-injected into the agent for self-correction.
    """

    # Sensor execution priority order (fast-fail first)
    SENSOR_ORDER = ["lint", "typecheck", "test", "build"]

    def __init__(self, config: HarnessConfig, workspace: Path):
        self._config = config
        self._workspace = workspace

    def run_all_sensors(self, changed_files: list[str] | None = None) -> HarnessReport:
        """Run all configured sensors in priority order.

        Args:
            changed_files: If provided, the lint sensor runs only on these
                files (incremental detection). Other sensors always run with
                their configured commands.

        Returns:
            HarnessReport with all sensor results aggregated. ``risk_level``
            is left at ``"low"`` and is expected to be overwritten by an
            external RiskScorer.
        """
        results: list[SensorResult] = []

        sensor_commands = {
            "lint": self._config.lint_command,
            "typecheck": self._config.typecheck_command,
            "test": self._config.test_command,
            "build": self._config.build_command,
        }

        for sensor_type in self.SENSOR_ORDER:
            command = sensor_commands.get(sensor_type, "")
            if not command:
                continue

            # For lint, support incremental detection on changed files only
            actual_command = command
            if sensor_type == "lint" and changed_files:
                actual_command = self._build_incremental_lint_command(command, changed_files)

            result = self.run_sensor(
                sensor_type=sensor_type,
                command=actual_command,
                timeout=self._config.feedback_timeout_seconds,
            )
            results.append(result)

        all_passed = all(r.passed for r in results)
        report = HarnessReport(
            sensors=results,
            all_passed=all_passed,
            risk_level="low",  # set externally by RiskScorer
            feedback_prompt="",
            doom_loop_detected=False,
        )

        if not all_passed:
            report.feedback_prompt = self.generate_feedback_prompt(report)

        return report

    def run_sensor(self, sensor_type: str, command: str, timeout: int) -> SensorResult:
        """Run a single sensor command.

        Uses ``subprocess.run`` with timeout protection. The combined
        stdout/stderr output is truncated to 2000 chars (head 500 + tail 1500)
        before being stored in the result.
        """
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._workspace),
            )
            duration = time.time() - start
            output = (proc.stdout + "\n" + proc.stderr).strip()
            output = self._truncate_output(output)

            return SensorResult(
                sensor_type=sensor_type,
                passed=(proc.returncode == 0),
                output=output,
                duration_seconds=round(duration, 2),
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            logger.warning("Sensor '%s' timed out after %ss", sensor_type, timeout)
            return SensorResult(
                sensor_type=sensor_type,
                passed=False,
                output=f"[TIMEOUT] Sensor '{sensor_type}' timed out after {timeout}s",
                duration_seconds=round(duration, 2),
                exit_code=-1,
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure as sensor failure
            duration = time.time() - start
            logger.exception("Sensor '%s' failed to run", sensor_type)
            return SensorResult(
                sensor_type=sensor_type,
                passed=False,
                output=f"[ERROR] Failed to run sensor: {exc}",
                duration_seconds=round(duration, 2),
                exit_code=-2,
            )

    def generate_feedback_prompt(self, report: HarnessReport) -> str:
        """Generate a feedback prompt summarising failed sensor results.

        The resulting markdown block is intended to be injected back into
        the agent so it can self-correct in a subsequent turn.
        """
        lines = ["## Harness Verification Failed\n"]
        lines.append("The following checks failed after your changes:\n")

        for sensor in report.sensors:
            if not sensor.passed:
                cmd = self._get_command_for_sensor(sensor.sensor_type)
                lines.append(f"### {sensor.sensor_type.title()} (`{cmd}`)")
                lines.append(f"Exit code: {sensor.exit_code}")
                lines.append(f"```\n{sensor.output}\n```\n")

        lines.append("Please fix these issues before proceeding.")
        return "\n".join(lines)

    def _truncate_output(self, output: str, max_length: int = 2000) -> str:
        """Truncate output preserving head (500) and tail (1500).

        The head usually carries the high-level summary while the tail
        carries the most actionable error details.
        """
        if len(output) <= max_length:
            return output
        head_size = 500
        tail_size = max_length - head_size - 20  # 20 chars reserved for the separator
        return (
            output[:head_size]
            + "\n\n... [truncated] ...\n\n"
            + output[-tail_size:]
        )

    def _build_incremental_lint_command(
        self, base_command: str, changed_files: list[str]
    ) -> str:
        """Build an incremental lint command targeting only changed files.

        Only applies if the lint tool is known to accept file arguments.
        Falls back to the full command if no files are supplied or too many
        files would be passed.
        """
        if not changed_files or len(changed_files) > 20:
            return base_command

        # Common lint tools that accept file args
        file_accepting_tools = ["ruff", "flake8", "eslint", "pylint", "mypy"]
        tool_name = base_command.split()[0] if base_command else ""

        if any(t in tool_name for t in file_accepting_tools):
            files_str = " ".join(changed_files)
            parts = base_command.split()
            if len(parts) >= 2:
                # Drop the trailing target path (usually "." or "src/") and
                # append the explicit changed-file list.
                tool_and_flags = " ".join(parts[:-1])
                return f"{tool_and_flags} {files_str}"

        return base_command

    def _get_command_for_sensor(self, sensor_type: str) -> str:
        """Get the configured command for a sensor type."""
        mapping = {
            "lint": self._config.lint_command,
            "typecheck": self._config.typecheck_command,
            "test": self._config.test_command,
            "build": self._config.build_command,
        }
        return mapping.get(sensor_type, "")


__all__ = ["HarnessSensorEngine"]
