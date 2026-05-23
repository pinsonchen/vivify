"""Declarative YAML probe — the workhorse of auto-heal's detection layer.

A YAML probe is a small file describing:

1. ``collect.steps``  — shell commands to run; captured output is bound to
   named variables ("``coverage_percent``", "``failure_count``", ...).
2. ``analyze.rules``  — Jinja2 ``when`` conditions over those variables; when
   true they emit an :class:`Issue` populated by another Jinja2 template.

This is enough for the 12 builtin probes in plan §7. Users who need richer
detection can drop a ``.py`` Python plugin into ``.auto-heal/probes/``.
"""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from jinja2 import Environment, StrictUndefined, Template, UndefinedError

from auto_heal.interfaces.probe import Probe, ProbeContext
from auto_heal.models import Issue, IssueLevel

logger = logging.getLogger(__name__)


# ── small jinja env, sandbox-style ───────────────────────────────────────────
_JINJA_ENV = Environment(
    autoescape=False, trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined
)


def _render(template_str: str, context: Mapping[str, Any]) -> str:
    return _JINJA_ENV.from_string(template_str).render(**context)


def _eval_when(expr: str, context: Mapping[str, Any]) -> bool:
    """Evaluate a Jinja ``when`` expression and coerce the result to a bool."""
    rendered = _render("{{ (" + expr + ") }}", context).strip().lower()
    if not rendered:
        return False
    return rendered not in ("false", "0", "none", "no")


# ── dataclasses describing the YAML schema ───────────────────────────────────
@dataclass
class _CollectStep:
    shell: str
    as_name: str = "_last"
    capture: str = "stdout"        # "stdout" | "stderr" | "returncode" | "raw"
    timeout_seconds: int = 60
    coerce: str = "auto"           # "auto" | "string" | "int" | "float" | "json" | "lines"
    when: Optional[str] = None     # Jinja expression — skip if false


@dataclass
class _AnalyzeRule:
    when: str
    emit: dict


@dataclass
class _ProbeSpec:
    id: str
    description: str = ""
    enabled_by_default: bool = True
    runs_on: tuple[str, ...] = ()
    auth_required: bool = False
    remediation_hint: str = ""
    config_overrides: dict = field(default_factory=dict)
    collect_steps: list[_CollectStep] = field(default_factory=list)
    analyze_rules: list[_AnalyzeRule] = field(default_factory=list)


def _parse_spec(source: str | Path | dict) -> _ProbeSpec:
    """Load a YAML file/string/dict into a :class:`_ProbeSpec`."""
    if isinstance(source, (str, Path)) and (
        isinstance(source, Path) or "\n" not in str(source)
    ):
        # Treat as path
        text = Path(source).read_text(encoding="utf-8")
    elif isinstance(source, dict):
        text = None
        data = source
    else:
        text = str(source)
    if text is not None:
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("probe YAML must be a mapping at the top level")
    if not data.get("id"):
        raise ValueError("probe YAML missing required field: id")
    spec = _ProbeSpec(
        id=str(data["id"]).strip(),
        description=str(data.get("description") or "").strip(),
        enabled_by_default=bool(data.get("enabled_by_default", True)),
        runs_on=tuple(data.get("runs_on") or ()),
        auth_required=bool(data.get("auth_required", False)),
        remediation_hint=str(data.get("remediation_hint") or "").strip(),
        config_overrides=dict(data.get("config_overrides") or {}),
    )
    collect = data.get("collect") or {}
    for raw_step in collect.get("steps", []) or []:
        if not isinstance(raw_step, dict):
            continue
        spec.collect_steps.append(
            _CollectStep(
                shell=str(raw_step.get("shell", "")).strip(),
                as_name=str(raw_step.get("as", "_last")).strip(),
                capture=str(raw_step.get("capture", "stdout")).strip(),
                timeout_seconds=int(raw_step.get("timeout_seconds", 60)),
                coerce=str(raw_step.get("coerce", "auto")).strip(),
                when=raw_step.get("when"),
            )
        )
    analyze = data.get("analyze") or {}
    for raw_rule in analyze.get("rules", []) or []:
        if not isinstance(raw_rule, dict):
            continue
        when = raw_rule.get("when")
        emit = raw_rule.get("emit") or {}
        if not when or not emit:
            continue
        spec.analyze_rules.append(_AnalyzeRule(when=str(when), emit=dict(emit)))
    return spec


# ── coercion ────────────────────────────────────────────────────────────────
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+([eE][-+]?\d+)?$")


def _coerce_value(raw: str, coerce: str) -> Any:
    raw = (raw or "").strip()
    if coerce == "string":
        return raw
    if coerce == "lines":
        return [line for line in raw.splitlines() if line.strip()]
    if coerce == "json":
        import json

        try:
            return json.loads(raw or "null")
        except (TypeError, ValueError):
            return None
    if coerce == "int":
        try:
            return int(raw or "0")
        except ValueError:
            return 0
    if coerce == "float":
        try:
            return float(raw or "0")
        except ValueError:
            return 0.0
    # auto
    if not raw:
        return ""
    if _INT_RE.match(raw):
        try:
            return int(raw)
        except ValueError:
            pass
    if _FLOAT_RE.match(raw):
        try:
            return float(raw)
        except ValueError:
            pass
    return raw


# ── concrete Probe implementation ────────────────────────────────────────────
class YamlProbe(Probe):
    """A :class:`Probe` driven entirely by a YAML spec."""

    def __init__(self, spec: _ProbeSpec):
        self._spec = spec
        # mirror onto class-style attributes the base ABC declares
        self.id = spec.id
        self.description = spec.description
        self.enabled_by_default = spec.enabled_by_default
        self.runs_on = spec.runs_on

    @property
    def auth_required(self) -> bool:
        return self._spec.auth_required

    @property
    def remediation_hint(self) -> str:
        return self._spec.remediation_hint

    # ── construction helpers ─────────────────────────────────────────────────
    @classmethod
    def from_file(cls, path: str | Path) -> "YamlProbe":
        return cls(_parse_spec(Path(path)))

    @classmethod
    def from_dict(cls, data: dict) -> "YamlProbe":
        return cls(_parse_spec(data))

    @classmethod
    def from_yaml(cls, text: str) -> "YamlProbe":
        return cls(_parse_spec(text))

    # ── Probe API ────────────────────────────────────────────────────────────
    def collect(self, ctx: ProbeContext) -> dict:
        spec = self._spec
        bindings: dict[str, Any] = {
            "config": _merge_config(spec.config_overrides, ctx.probe_config),
        }
        for step in spec.collect_steps:
            try:
                if step.when and not _eval_when(step.when, bindings):
                    continue
            except (UndefinedError, Exception) as e:
                ctx.logger.debug("[probe %s] skip-when failed: %s", spec.id, e)
                continue

            try:
                shell_cmd = _render(step.shell, bindings)
            except (UndefinedError, Exception) as e:
                ctx.logger.warning("[probe %s] shell render failed: %s", spec.id, e)
                bindings[step.as_name] = ""
                continue

            try:
                proc = subprocess.run(
                    shell_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=str(ctx.repo_root),
                    timeout=step.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                ctx.logger.warning(
                    "[probe %s] step %s timed out (%ds)",
                    spec.id, step.as_name, step.timeout_seconds,
                )
                bindings[step.as_name] = ""
                continue
            except Exception as e:
                ctx.logger.warning("[probe %s] step %s exec error: %s", spec.id, step.as_name, e)
                bindings[step.as_name] = ""
                continue

            if step.capture == "stderr":
                raw_out = proc.stderr or ""
            elif step.capture == "returncode":
                raw_out = str(proc.returncode)
            elif step.capture == "raw":
                bindings[step.as_name] = {
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "returncode": proc.returncode,
                }
                continue
            else:
                raw_out = proc.stdout or ""
            bindings[step.as_name] = _coerce_value(raw_out, step.coerce)
            # Also expose the most recent return code under a fixed name for
            # rules that need it (`_last_rc`).
            bindings["_last_rc"] = proc.returncode

        return bindings

    def analyze(self, raw: dict, ctx: ProbeContext) -> list[Issue]:
        issues: list[Issue] = []
        spec = self._spec
        for rule in spec.analyze_rules:
            try:
                fired = _eval_when(rule.when, raw)
            except (UndefinedError, Exception) as e:
                ctx.logger.debug("[probe %s] rule when failed: %s", spec.id, e)
                continue
            if not fired:
                continue
            emit = dict(rule.emit)
            try:
                category = _render(str(emit.get("category", spec.id)), raw)
                level_str = _render(str(emit.get("level", "MEDIUM")), raw).upper()
                title = _render(str(emit.get("title", spec.id)), raw)
                description = _render(str(emit.get("description", "")), raw)
            except (UndefinedError, Exception) as e:
                ctx.logger.debug("[probe %s] emit render failed: %s", spec.id, e)
                continue
            try:
                level = IssueLevel(level_str)
            except ValueError:
                level = IssueLevel.MEDIUM
            data_field = emit.get("data") or {}
            data_resolved = _resolve_data(data_field, raw)
            issues.append(
                Issue.factory(
                    category=category.strip() or spec.id,
                    level=level,
                    title=title.strip(),
                    description=description.strip(),
                    data=data_resolved,
                    source_probe=spec.id,
                )
            )
        return issues


def _resolve_data(data_field: Any, context: Mapping[str, Any]) -> dict:
    """Render Jinja in each leaf value of the YAML ``data:`` block."""
    if not isinstance(data_field, dict):
        return {"_raw": str(data_field)}
    out: dict[str, Any] = {}
    for k, v in data_field.items():
        if isinstance(v, str) and "{{" in v:
            try:
                out[k] = _render(v, context)
            except (UndefinedError, Exception):
                out[k] = v
        elif isinstance(v, dict):
            out[k] = _resolve_data(v, context)
        else:
            out[k] = v
    return out


def _merge_config(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict:
    merged = dict(defaults or {})
    merged.update(overrides or {})
    return merged


__all__ = ["YamlProbe"]
