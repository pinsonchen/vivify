"""Load ``.vivify.yml`` (with env-var overrides) into :class:`VivifyConfig`."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from vivify.config.schema import VivifyConfig

_ENV_PREFIX = "VIVIFY__"


def _set_in(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.lower().split("__")
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):  # collision: ignore
            return
    node[parts[-1]] = value


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (override wins on conflicts)."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merge_env(base: dict) -> dict:
    """Apply ``VIVIFY__<dotted>`` env vars onto ``base`` in-place."""
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        dotted = key[len(_ENV_PREFIX):]
        # Best-effort type coercion
        if raw.lower() in ("true", "false"):
            value: Any = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
        _set_in(base, dotted, value)
    return base


def find_state_dir(start: Path | str | None = None) -> Path | None:
    """Walk up from ``start`` (default: cwd) looking for a ``.vivify`` directory.

    Returns the resolved Path if found, otherwise ``None``.
    """
    current = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        target = candidate / ".vivify"
        if target.is_dir():
            return target
    return None


def load_config(path: Path | str | None = None) -> VivifyConfig:
    """Load ``.vivify.yml`` if it exists; otherwise return defaults."""
    raw: dict = {}
    if path is None:
        candidate = Path(".vivify.yml")
    else:
        candidate = Path(path)
    if candidate.exists():
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load .vivify.yml") from e
        with candidate.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{candidate} must contain a YAML mapping at the root")

    # 合并 .vivify-advanced.yml（如果存在），advanced 覆盖主配置同名键
    advanced_path = candidate.parent / ".vivify-advanced.yml"
    if advanced_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]
            with advanced_path.open("r", encoding="utf-8") as afh:
                advanced_raw = yaml.safe_load(afh) or {}
            if isinstance(advanced_raw, dict):
                raw = _deep_merge(raw, advanced_raw)
        except Exception:
            pass  # advanced 文件加载失败不阻塞主配置

    raw = _merge_env(raw)
    return VivifyConfig(**raw)


__all__ = ["load_config", "find_state_dir", "_deep_merge"]
