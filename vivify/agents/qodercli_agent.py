"""``CodingAgent`` implementation that wraps Qoder CLI (``qodercli``).

This is a near-verbatim port of ``channels-monitor/vivify/healer.py::heal``,
made project-agnostic:

* Binary path / model / max-turns / extra args are configurable.
* Working directory comes from the caller (worktree path), not a global.
* Subprocess environment is tagged with ``VIVIFY_AGENT=1`` so
  :mod:`vivify.agents.slot_manager` can reliably count concurrent runs.
* Stdout has ``[hook timing]`` lines stripped and is returned via ``AgentResult``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from vivify.agents.remote_session import RemoteSession, RemoteSessionManager  # noqa: F401
from vivify.agents.slot_manager import AGENT_ENV_TAG, wait_for_slot
from vivify.interfaces.agent import CodingAgent
from vivify.models import AgentResult

logger = logging.getLogger(__name__)


@dataclass
class QoderCliConfig:
    """Configuration for :class:`QoderCliAgent` (mirrors ``.vivify.yml::agent.qodercli``)."""

    binary_path: str = "qodercli"
    """Either an absolute path or a name resolved via ``shutil.which``."""

    model: str = "ultimate"
    max_turns_default: int = 30
    timeout_seconds_default: int = 1800
    """Hard subprocess timeout. ``0`` disables the timeout."""

    extra_args: Sequence[str] = field(default_factory=lambda: ("--yolo", "-q"))
    """Extra CLI flags appended to every invocation."""

    max_concurrent_processes: int = 10
    slot_wait_timeout_seconds: int = 300
    slot_poll_interval_seconds: int = 15
    auto_trust_workspace: bool = True
    """When ``True`` we pipe ``yes\n`` on stdin to auto-trust new workdirs."""

    # ── Remote (cloud) execution ─────────────────────────────────────────────
    use_remote: bool = False
    """Route ``heal`` calls through qodercli cloud remote sessions."""
    remote_poll_interval: int = 15
    """Seconds between status-check polls for a remote session."""
    remote_timeout: int = 900
    """Maximum wall-clock seconds to wait for a remote session."""
    max_concurrent_remote: int = 3
    """Maximum number of parallel remote sessions (informational for callers)."""
    plan_agent_for_decompose: bool = True
    """Use the built-in Plan agent when decomposing goals."""


class QoderCliAgent(CodingAgent):
    """Default :class:`CodingAgent` implementation."""

    def __init__(self, cfg: QoderCliConfig | None = None):
        self.cfg = cfg or QoderCliConfig()
        self._remote_mgr: Optional[RemoteSessionManager] = None
        if self.cfg.use_remote:
            self._remote_mgr = RemoteSessionManager(
                binary=self.cfg.binary_path,
                model=self.cfg.model,
                extra_args=list(self.cfg.extra_args) or ["--yolo", "-q"],
            )

    @property
    def remote_mgr(self) -> Optional[RemoteSessionManager]:
        """Lazily-constructed remote session manager (``None`` when not in remote mode)."""
        return self._remote_mgr

    # ── CodingAgent ──────────────────────────────────────────────────────────
    def name(self) -> str:
        return "qodercli"

    def healthcheck(self) -> tuple[bool, str]:
        binary = self._resolve_binary()
        if not binary:
            return False, f"qodercli binary not found (configured: {self.cfg.binary_path!r})"
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return False, f"qodercli --version failed: {e}"
        if result.returncode != 0:
            return False, f"qodercli --version exit={result.returncode}: {result.stderr.strip()[:200]}"
        return True, result.stdout.strip().splitlines()[0] if result.stdout.strip() else "ok"

    def heal(
        self,
        prompt: str,
        *,
        max_turns: int,
        category: str,
        workspace: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[int] = None,
        agent_name: Optional[str] = None,
    ) -> AgentResult:
        """Execute a healing task, routing to remote or local mode as configured."""
        if self.cfg.use_remote and self._remote_mgr:
            result = self._heal_remote(prompt, workspace=workspace, max_turns=max_turns)
            if result.success:
                return result
            # Fallback to local execution on remote failure
            logger.warning(
                "Remote session failed (error=%s), falling back to local execution",
                result.error,
            )
        return self._heal_local(
            prompt,
            max_turns=max_turns,
            category=category,
            workspace=workspace,
            env=env,
            timeout_seconds=timeout_seconds,
            agent_name=agent_name,
        )

    def _heal_remote(
        self,
        prompt: str,
        *,
        workspace: Path,
        max_turns: int = 100,
    ) -> AgentResult:
        """Execute via a qodercli cloud remote session."""
        import time as _time

        assert self._remote_mgr is not None  # guarded by caller
        t0 = _time.time()
        try:
            session = self._remote_mgr.create_session(
                task=prompt, workspace=workspace, max_turns=max_turns
            )
            session = self._remote_mgr.wait_for_completion(
                session,
                poll_interval=self.cfg.remote_poll_interval,
                timeout=self.cfg.remote_timeout,
                workspace=workspace,
            )
        except Exception as exc:  # create_session / network failures
            elapsed = _time.time() - t0
            logger.exception("Remote session launch failed: %s", exc)
            return AgentResult(
                success=False, output="", exit_code=-1,
                error=str(exc), duration_seconds=elapsed,
            )

        elapsed = _time.time() - t0
        if session.status == "completed":
            output = self._remote_mgr.get_result(session.session_id, workspace)
            logger.info(
                "Remote session %s completed in %.1fs", session.session_id, elapsed
            )
            return AgentResult(
                success=True, output=output, exit_code=0,
                error=None, duration_seconds=elapsed,
            )
        else:
            msg = f"Remote session failed: {session.session_id} (status={session.status})"
            logger.error(msg)
            return AgentResult(
                success=False, output="", exit_code=-1,
                error=msg, duration_seconds=elapsed,
            )

    def _heal_local(
        self,
        prompt: str,
        *,
        max_turns: int,
        category: str,
        workspace: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[int] = None,
        agent_name: Optional[str] = None,
    ) -> AgentResult:
        """Execute locally via a blocking qodercli subprocess (original logic)."""
        binary = self._resolve_binary()
        if not binary:
            return AgentResult(
                success=False,
                output="",
                exit_code=-1,
                error=f"qodercli binary not found at {self.cfg.binary_path!r}",
                duration_seconds=0.0,
            )

        workspace = Path(workspace)
        if not workspace.exists():
            return AgentResult(
                success=False,
                output="",
                exit_code=-1,
                error=f"workspace does not exist: {workspace}",
                duration_seconds=0.0,
            )

        cmd = [
            binary,
            "-p", prompt,
            *self.cfg.extra_args,
            "--model", self.cfg.model,
            "--max-turns", str(int(max_turns or self.cfg.max_turns_default)),
            "-w", str(workspace),
        ]
        if agent_name:
            cmd.extend(["--agent", agent_name])

        # Slot gating — never exceed the configured concurrent cap.
        if self.cfg.max_concurrent_processes > 0:
            granted = wait_for_slot(
                category=category,
                max_concurrent=self.cfg.max_concurrent_processes,
                timeout_seconds=self.cfg.slot_wait_timeout_seconds,
                poll_interval_seconds=self.cfg.slot_poll_interval_seconds,
                binary_basename=Path(binary).name,
            )
            if not granted:
                msg = (
                    f"qodercli concurrency cap reached ({self.cfg.max_concurrent_processes}); "
                    f"waited {self.cfg.slot_wait_timeout_seconds}s without a free slot"
                )
                logger.error("[%s] %s", category, msg)
                return AgentResult(success=False, output="", exit_code=-1, error=msg, duration_seconds=0.0)

        run_env = self._build_env(env)
        timeout = (
            None
            if timeout_seconds == 0 or self.cfg.timeout_seconds_default == 0
            else int(timeout_seconds or self.cfg.timeout_seconds_default)
        )

        logger.info(
            "[%s] invoking qodercli (max_turns=%s, prompt_len=%d, workspace=%s)",
            category, cmd[cmd.index("--max-turns") + 1], len(prompt), workspace,
        )

        import time as _time
        t0 = _time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input="yes\n" if self.cfg.auto_trust_workspace else None,
                env=run_env,
                cwd=str(workspace),
            )
        except subprocess.TimeoutExpired:
            elapsed = _time.time() - t0
            logger.error("[%s] qodercli timed out after %.1fs", category, elapsed)
            return AgentResult(
                success=False, output="", exit_code=-1,
                error=f"qodercli timed out after {timeout}s",
                duration_seconds=elapsed,
            )
        except Exception as e:  # subprocess failure / OSError / etc
            elapsed = _time.time() - t0
            logger.exception("[%s] qodercli launch failed: %s", category, e)
            return AgentResult(
                success=False, output="", exit_code=-1,
                error=str(e), duration_seconds=elapsed,
            )

        elapsed = _time.time() - t0
        output = _filter_hooks(result.stdout)
        err_output = (result.stderr or "").strip()

        if result.returncode != 0:
            logger.warning(
                "[%s] qodercli exit=%d: %s",
                category, result.returncode, err_output[:200],
            )
            return AgentResult(
                success=False, output=output, exit_code=result.returncode,
                error=err_output[:500] or None, duration_seconds=elapsed,
            )

        logger.info("[%s] qodercli ok (%.1fs, %d chars)", category, elapsed, len(output))
        return AgentResult(
            success=True, output=output, exit_code=0, error=None, duration_seconds=elapsed,
        )

    # ── helpers ─────────────────────────────────────────────────────────────
    def _resolve_binary(self) -> Optional[str]:
        path = self.cfg.binary_path
        if os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        which = shutil.which(path)
        return which

    def _build_env(self, extra: Optional[Mapping[str, str]]) -> dict[str, str]:
        env = dict(os.environ)
        env["TERM"] = "dumb"
        env[AGENT_ENV_TAG] = "1"
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return env


def _filter_hooks(output: str) -> str:
    """Strip `[hook timing]` lines emitted by qodercli's hook subsystem."""
    if not output:
        return ""
    return "\n".join(
        line for line in output.splitlines() if not line.startswith("[hook timing]")
    ).strip()


__all__ = ["QoderCliAgent", "QoderCliConfig"]
