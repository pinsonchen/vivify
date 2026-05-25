"""Remote session manager for qodercli cloud execution."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RemoteSession:
    """Represents a qodercli cloud remote session."""

    session_id: str
    task: str
    url: str
    status: str = "running"  # running | completed | failed | unknown
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: Optional[str] = None


class RemoteSessionManager:
    """Manage qodercli remote sessions for async task execution."""

    def __init__(
        self,
        binary: str = "qodercli",
        model: str = "ultimate",
        extra_args: list[str] | None = None,
    ):
        self.binary = binary
        self.model = model
        self.extra_args = extra_args or ["--yolo", "-q"]

    # ── public API ────────────────────────────────────────────────────────────

    def create_session(
        self, task: str, workspace: Path, max_turns: int = 100
    ) -> RemoteSession:
        """Create a new cloud remote session.

        Runs ``qodercli --remote <task> -w <workspace> --model <model>
        --max-turns <n> <extra_args>`` and parses the printed Session ID / URL.
        """
        cmd = [
            self.binary,
            "--remote", task,
            "--model", self.model,
            "--max-turns", str(max_turns),
            "-w", str(workspace),
            *self.extra_args,
        ]
        logger.info("Creating remote session: %s", task[:80])

        env = os.environ.copy()
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create remote session: {result.stderr[:300]}"
            )

        # Some info may be written to stderr by the binary
        output = result.stdout + result.stderr
        session_id = self._parse_field(output, r"Session ID:\s*(\S+)")
        url = self._parse_field(output, r"URL:\s*(https://\S+)")

        if not session_id:
            raise RuntimeError(
                f"Could not parse session ID from output: {output[:200]}"
            )

        session = RemoteSession(
            session_id=session_id,
            task=task,
            url=url or f"https://qoder.com/agents/session/{session_id}",
        )
        logger.info("Remote session created: %s (%s)", session_id, session.url)
        return session

    def check_status(self, session_id: str) -> str:
        """Check whether a remote session has completed.

        Returns one of ``'running'``, ``'completed'``, ``'failed'``,
        or ``'unknown'``.
        """
        try:
            return self._check_via_teleport(session_id)
        except Exception as exc:
            logger.warning("Failed to check session %s: %s", session_id, exc)
            return "unknown"

    def get_result(self, session_id: str, workspace: Path) -> str:
        """Return a summary of completed work by inspecting recent git commits."""
        try:
            proc = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return proc.stdout.strip()
        except Exception:
            return ""

    def list_sessions(self) -> str:
        """Return raw output of ``qodercli --list-sessions``."""
        try:
            proc = subprocess.run(
                [self.binary, "--list-sessions"],
                capture_output=True,
                text=True,
                timeout=15,
                env=os.environ.copy(),
            )
            return proc.stdout.strip()
        except Exception as exc:
            logger.warning("list_sessions failed: %s", exc)
            return ""

    def wait_for_completion(
        self,
        session: RemoteSession,
        poll_interval: int = 15,
        timeout: int = 900,
    ) -> RemoteSession:
        """Poll until *session* completes or the wall-clock *timeout* expires."""
        start = time.time()
        while time.time() - start < timeout:
            status = self.check_status(session.session_id)
            session.status = status
            if status in ("completed", "failed"):
                return session
            logger.debug(
                "Remote session %s still %s, next poll in %ds",
                session.session_id, status, poll_interval,
            )
            time.sleep(poll_interval)

        session.status = "failed"
        logger.warning(
            "Remote session %s timed out after %ds",
            session.session_id, timeout,
        )
        return session

    # ── private helpers ───────────────────────────────────────────────────────

    def _check_via_teleport(self, session_id: str) -> str:
        """Attach briefly to the session to probe its current status."""
        cmd = [
            self.binary, "--teleport", session_id,
            "-p", "status",
            "--max-turns", "1",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                env=os.environ.copy(),
            )
            output = proc.stdout.lower()
            if any(kw in output for kw in ("completed", "done", "finished")):
                return "completed"
            if any(kw in output for kw in ("error", "failed")):
                return "failed"
            return "running"
        except subprocess.TimeoutExpired:
            return "running"
        except Exception:
            return "unknown"

    @staticmethod
    def _parse_field(text: str, pattern: str) -> str:
        """Extract the first capture group from *text* using *pattern*."""
        match = re.search(pattern, text)
        return match.group(1) if match else ""


__all__ = ["RemoteSession", "RemoteSessionManager"]
