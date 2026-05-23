"""Probe registry — discovers built-in YAML probes and user plugins.

Discovery order:

1. **Built-in YAML** under ``vivify/probes/builtin/*.yml`` (shipped wheel).
2. **User YAML** under ``<repo>/.vivify/probes/*.yml``.
3. **User Python** under ``<repo>/.vivify/probes/*.py`` — each module is
   imported and any top-level :class:`Probe` subclass instance assigned to a
   ``PROBE`` attribute (or list of probes assigned to ``PROBES``) is collected.

The kernel resolves the final enabled set by intersecting ``probes.enabled`` in
``.vivify.yml`` with everything the registry could find.
"""
from __future__ import annotations

import importlib.util
import logging
from importlib import resources
from pathlib import Path
from typing import Iterable, Optional

from vivify.interfaces.probe import Probe
from vivify.probes.base import YamlProbe

logger = logging.getLogger(__name__)


class ProbeRegistry:
    def __init__(self) -> None:
        self._probes: dict[str, Probe] = {}

    # ── lookup ───────────────────────────────────────────────────────────────
    def all(self) -> list[Probe]:
        return list(self._probes.values())

    def get(self, probe_id: str) -> Optional[Probe]:
        return self._probes.get(probe_id)

    def ids(self) -> list[str]:
        return sorted(self._probes.keys())

    # ── discovery ────────────────────────────────────────────────────────────
    def load_builtins(self) -> int:
        """Load every ``*.yml`` shipped under ``vivify/probes/builtin``."""
        count = 0
        try:
            pkg = resources.files("vivify.probes.builtin")
        except ModuleNotFoundError:
            return 0
        for entry in pkg.iterdir():
            if not entry.name.endswith((".yml", ".yaml")):
                continue
            try:
                text = entry.read_text(encoding="utf-8")
                probe = YamlProbe.from_yaml(text)
            except Exception as e:
                logger.warning("Skipping builtin probe %s: %s", entry.name, e)
                continue
            self._register(probe)
            count += 1
        return count

    def load_user_dir(self, user_dir: Path | str | None) -> int:
        """Load YAML + Python probes from a user directory (typically ``.vivify/probes``)."""
        if not user_dir:
            return 0
        path = Path(user_dir)
        if not path.is_dir():
            return 0
        count = 0
        for entry in sorted(path.iterdir()):
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.suffix.lower() in (".yml", ".yaml"):
                try:
                    self._register(YamlProbe.from_file(entry))
                    count += 1
                except Exception as e:
                    logger.warning("Skipping user YAML probe %s: %s", entry.name, e)
            elif entry.suffix.lower() == ".py":
                count += self._load_python_plugin(entry)
        return count

    # ── internals ────────────────────────────────────────────────────────────
    def _register(self, probe: Probe) -> None:
        if not getattr(probe, "id", None):
            logger.warning("Skipping probe without an id: %r", probe)
            return
        if probe.id in self._probes:
            logger.info("Overriding probe %s (user override / duplicate)", probe.id)
        self._probes[probe.id] = probe

    def _load_python_plugin(self, path: Path) -> int:
        spec = importlib.util.spec_from_file_location(f"vivify_userprobe_{path.stem}", path)
        if not spec or not spec.loader:
            return 0
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning("Failed to import user probe %s: %s", path.name, e)
            return 0
        loaded = 0
        for attr in ("PROBES", "PROBE"):
            obj = getattr(module, attr, None)
            if obj is None:
                continue
            iterable: Iterable
            if isinstance(obj, (list, tuple)):
                iterable = obj
            else:
                iterable = [obj]
            for candidate in iterable:
                if isinstance(candidate, Probe):
                    self._register(candidate)
                    loaded += 1
        if loaded == 0:
            logger.debug("User probe %s defined no PROBE / PROBES export", path.name)
        return loaded


def build_default_registry(user_dir: Path | str | None = None) -> ProbeRegistry:
    """Convenience: built-ins + optional user dir."""
    reg = ProbeRegistry()
    reg.load_builtins()
    if user_dir:
        reg.load_user_dir(user_dir)
    return reg


__all__ = ["ProbeRegistry", "build_default_registry"]
