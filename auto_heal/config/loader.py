"""Load ``.auto-heal.yml`` (with env-var overrides) into :class:`AutoHealConfig`."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from auto_heal.config.schema import AutoHealConfig

_ENV_PREFIX = "AUTO_HEAL__"


def _set_in(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.lower().split("__")
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):  # collision: ignore
            return
    node[parts[-1]] = value


def _merge_env(base: dict) -> dict:
    """Apply ``AUTO_HEAL__<dotted>`` env vars onto ``base`` in-place."""
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


def load_config(path: Path | str | None = None) -> AutoHealConfig:
    """Load ``.auto-heal.yml`` if it exists; otherwise return defaults."""
    raw: dict = {}
    if path is None:
        candidate = Path(".auto-heal.yml")
    else:
        candidate = Path(path)
    if candidate.exists():
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load .auto-heal.yml") from e
        with candidate.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{candidate} must contain a YAML mapping at the root")
    raw = _merge_env(raw)
    return AutoHealConfig(**raw)


__all__ = ["load_config"]
