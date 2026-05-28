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
from typing import Dict, Mapping, Optional, Sequence

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
    permission_mode: str = "bypass_permissions"
    """Permission mode for qodercli (default/accept_edits/bypass_permissions/dont_ask/plan/auto)."""

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

    wiki_path: str = ""
    """Optional repo-relative path to ``qodercli wiki`` output (e.g. ``.qoder/repowiki/zh``).

    When set and the metadata exists, a short architecture summary is
    prepended to every prompt sent through :meth:`QoderCliAgent._heal_local`,
    grounding feature-pipeline reasoning in project-specific context.
    """

    # ── Differentiated parameter injection (per-category) ───────────────────
    reasoning_effort_by_category: Dict[str, str] = field(default_factory=dict)
    """Per-category ``--reasoning-effort`` value (e.g. {'fix_issue': 'high'})."""

    system_prompt_suffix: str = ""
    """When non-empty, appended to qodercli system prompt via ``--append-system-prompt``."""

    max_output_tokens_by_category: Dict[str, int] = field(default_factory=dict)
    """Per-category ``--max-output-tokens`` value."""

    agent_for_category: Dict[str, str] = field(default_factory=dict)
    """Per-category ``--agent`` value (fallback when caller does not pass agent_name)."""

    max_attachments: int = 3
    """Maximum number of ``--attachment`` arguments injected from knowledge graph."""

    # ── Harness feedforward guides ─────────────────────────────────────────
    guides_dir: str = ""
    """Directory containing harness guide ``.md`` files.

    Resolved relative to the workspace passed to :meth:`heal`. Empty string
    disables guide injection regardless of ``inject_guides_to_prompt``.
    """

    inject_guides_to_prompt: bool = False
    """When ``True`` and ``guides_dir`` is set, append matching guides text
    to ``--append-system-prompt``."""


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
                permission_mode=self.cfg.permission_mode,
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
        category: str = "fix_issue",
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
        category: str = "fix_issue",
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

        cmd = self._build_cmd(
            prompt,
            workspace=workspace,
            category=category,
            max_turns=max_turns,
            agent_name=agent_name,
        )

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
    def _build_cmd(
        self,
        prompt: str,
        *,
        workspace: Path,
        category: str,
        max_turns: Optional[int] = None,
        agent_name: Optional[str] = None,
    ) -> list[str]:
        """Build qodercli command line with per-category differentiated parameter injection."""
        binary = self.cfg.binary_path
        cmd = [
            binary,
            "-p", self._augment_prompt_with_knowledge(prompt, workspace),
            *self.cfg.extra_args,
            "--model", self.cfg.model,
            "--max-turns", str(int(max_turns or self.cfg.max_turns_default)),
            "--permission-mode", self.cfg.permission_mode,
            "-w", str(workspace),
        ]

        # 1. reasoning-effort (per category)
        effort_map = getattr(self.cfg, "reasoning_effort_by_category", {}) or {}
        effort = effort_map.get(category)
        if effort:
            cmd.extend(["--reasoning-effort", effort])

        # 2. append-system-prompt (config suffix + dynamic conventions + guides)
        suffix = getattr(self.cfg, "system_prompt_suffix", "") or ""
        conventions_suffix = self._get_conventions_suffix(workspace)
        guides_suffix = self._get_guides_suffix(workspace, category)
        suffix_parts = [s for s in (suffix, conventions_suffix, guides_suffix) if s]
        if suffix_parts:
            cmd.extend(["--append-system-prompt", "\n".join(suffix_parts)])

        # 3. max-output-tokens (per category)
        max_tokens_map = getattr(self.cfg, "max_output_tokens_by_category", {}) or {}
        max_tokens = max_tokens_map.get(category)
        if max_tokens:
            cmd.extend(["--max-output-tokens", str(max_tokens)])

        # 4. agent selection (per category, explicit caller wins)
        agent_map = getattr(self.cfg, "agent_for_category", {}) or {}
        effective_agent = agent_name or agent_map.get(category)
        if effective_agent:
            cmd.extend(["--agent", effective_agent])

        # 5. attachments (recommended files from knowledge graph)
        max_attachments = int(getattr(self.cfg, "max_attachments", 3) or 0)
        if max_attachments > 0:
            attachments = self._get_attachments(prompt, workspace)
            for file_path in attachments[:max_attachments]:
                cmd.extend(["--attachment", str(file_path)])

        return cmd

    def _get_attachments(self, prompt: str, workspace: Path) -> list[Path]:
        """Get knowledge-graph-recommended files relevant to ``prompt`` as attachments.

        Uses :meth:`KnowledgeContextProvider.recommend_files` (introduced in Task #85).
        Returns an empty list when the method is unavailable or any error occurs.
        """
        try:
            from vivify.knowledge.context_provider import (  # noqa: WPS433
                KnowledgeContextProvider,
            )
            provider = KnowledgeContextProvider(workspace)
            if getattr(provider, "graph", None) and hasattr(provider, "recommend_files"):
                max_files = int(getattr(self.cfg, "max_attachments", 3) or 3)
                return list(
                    provider.recommend_files(
                        prompt[:200], workspace, max_files=max_files
                    )
                )
        except Exception:
            pass
        return []

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

    def _augment_prompt_with_knowledge(
        self,
        prompt: str,
        workspace: Path,
        feature_title: str = "",
        feature_description: str = "",
        issue_category: str = "",
        issue_title: str = "",
    ) -> str:
        """Inject knowledge graph context into prompt.

        Strategy:
        1. Try targeted entity-level matching (get_targeted_context) first
        2. If empty, fallback to module-level matching (get_context_for_feature)
        3. Append historical experience if issue info is provided
        4. Fallback to wiki when no knowledge graph exists

        When ``feature_title`` / ``feature_description`` are not supplied,
        the leading slice of ``prompt`` is used for relevance matching.
        Any failure returns the original prompt unchanged.
        """
        try:
            from vivify.knowledge.context_provider import (  # noqa: WPS433
                KnowledgeContextProvider,
                get_knowledge_context,
            )
        except Exception:  # pragma: no cover - defensive
            return self._augment_prompt_with_wiki(prompt, workspace)

        title = feature_title or (prompt[:200] if prompt else "")
        description = feature_description

        knowledge_block = ""
        try:
            provider = KnowledgeContextProvider(Path(workspace))
            # Priority 1: Targeted entity-level matching
            targeted = provider.get_targeted_context(title, description)
            if targeted:
                knowledge_block = targeted
            else:
                # Priority 2: Module-level matching (existing logic)
                knowledge_block = get_knowledge_context(
                    project_root=Path(workspace),
                    feature_title=title,
                    feature_description=description,
                )

            # Append historical experience if issue info provided
            if issue_category and issue_title:
                historical = self._get_historical_context(
                    provider, issue_category, issue_title
                )
                if historical:
                    knowledge_block = (
                        f"{knowledge_block}\n\n{historical}"
                        if knowledge_block
                        else historical
                    )
        except Exception:  # pragma: no cover - defensive
            knowledge_block = ""

        if knowledge_block:
            return f"{knowledge_block}\n\n---\n\n{prompt}"

        # Fallback: legacy wiki injection when no knowledge graph exists.
        return self._augment_prompt_with_wiki(prompt, workspace)

    def _get_historical_context(
        self,
        provider,
        category: str,
        title: str,
    ) -> str:
        """Get historical context using storage if available."""
        try:
            storage = getattr(self, "storage", None)
            if storage is None:
                return ""
            return provider.get_historical_context(category, title, storage)
        except Exception:
            return ""

    def _get_conventions_suffix(self, workspace: Path) -> str:
        """Get conventions text for --append-system-prompt from knowledge graph."""
        try:
            from vivify.knowledge.context_provider import (  # noqa: WPS433
                KnowledgeContextProvider,
            )
            provider = KnowledgeContextProvider(Path(workspace))
            return provider.get_conventions_for_system_prompt()
        except Exception:
            return ""

    def _get_guides_suffix(self, workspace: Path, category: str) -> str:
        """Return harness feedforward guides matching ``category``.

        Returns an empty string when ``inject_guides_to_prompt`` is disabled,
        ``guides_dir`` is empty, the directory does not exist, or no guides
        match the category. Any failure is silently absorbed.
        """
        if not getattr(self.cfg, "inject_guides_to_prompt", False):
            return ""
        guides_dir_cfg = (getattr(self.cfg, "guides_dir", "") or "").strip()
        if not guides_dir_cfg:
            return ""
        try:
            from vivify.harness.guides import GuidesManager  # noqa: WPS433
            guides_path = Path(guides_dir_cfg)
            if not guides_path.is_absolute():
                guides_path = Path(workspace) / guides_path
            if not guides_path.exists():
                return ""
            return GuidesManager(guides_path).get_guides_for_category(category) or ""
        except Exception:
            return ""

    def _augment_prompt_with_wiki(self, prompt: str, workspace: Path) -> str:
        """Prepend a short project-architecture block when ``wiki_path`` is configured.

        Failures (missing/unreadable metadata) are silently ignored: we
        return the original prompt unchanged. Imported lazily so the
        agent stays usable when the intelligence package is unavailable.
        """
        wiki_path = (self.cfg.wiki_path or "").strip()
        if not wiki_path:
            return prompt
        try:
            from vivify.intelligence.wiki_generator import (  # noqa: WPS433
                load_wiki_context_if_available,
            )
        except Exception:  # pragma: no cover - defensive
            return prompt
        try:
            ctx = load_wiki_context_if_available(
                Path(workspace), wiki_dir=wiki_path,
            )
        except Exception:  # pragma: no cover - defensive
            return prompt
        if ctx is None or ctx.is_empty():
            return prompt
        block = ctx.to_prompt_block(
            max_overview_chars=800,
            max_source_files=15,
            max_catalogs=10,
        )
        if not block:
            return prompt
        return (
            "以下是项目架构上下文（由 qodercli wiki 生成），供你在开发时参考：\n\n"
            f"{block}\n\n---\n\n{prompt}"
        )


def _filter_hooks(output: str) -> str:
    """Strip `[hook timing]` lines emitted by qodercli's hook subsystem."""
    if not output:
        return ""
    return "\n".join(
        line for line in output.splitlines() if not line.startswith("[hook timing]")
    ).strip()


__all__ = ["QoderCliAgent", "QoderCliConfig"]
