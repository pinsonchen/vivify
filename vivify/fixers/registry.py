"""Fixer registry — discovers built-in fixers and user plugins."""
from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from pathlib import Path
from typing import Iterable, Optional

from vivify.interfaces.fixer import Fixer

logger = logging.getLogger(__name__)


class FixerRegistry:
    def __init__(self) -> None:
        self._fixers: dict[str, Fixer] = {}

    def all(self) -> list[Fixer]:
        return list(self._fixers.values())

    def get(self, fixer_id: str) -> Optional[Fixer]:
        return self._fixers.get(fixer_id)

    def ids(self) -> list[str]:
        return sorted(self._fixers.keys())

    # ── discovery ────────────────────────────────────────────────────────────
    def load_builtins(self) -> int:
        """Import every module under ``vivify.fixers.builtin`` and collect
        instances assigned to ``FIXER`` / ``FIXERS``."""
        import vivify.fixers.builtin as pkg
        count = 0
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            if mod_info.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"{pkg.__name__}.{mod_info.name}")
            except Exception as e:
                logger.warning("Skipping builtin fixer module %s: %s", mod_info.name, e)
                continue
            count += self._collect_from_module(module)
        return count

    def load_user_dir(self, user_dir: Path | str | None) -> int:
        if not user_dir:
            return 0
        path = Path(user_dir)
        if not path.is_dir():
            return 0
        count = 0
        for entry in sorted(path.iterdir()):
            if entry.suffix.lower() != ".py" or entry.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(
                f"vivify_userfixer_{entry.stem}", entry
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                logger.warning("Failed to import user fixer %s: %s", entry.name, e)
                continue
            count += self._collect_from_module(module)
        return count

    # ── candidates for an Issue ──────────────────────────────────────────────
    def candidates_for(self, category: str) -> list[Fixer]:
        return [f for f in self._fixers.values() if category in f.handles_categories]

    # ── internals ────────────────────────────────────────────────────────────
    def _register(self, fixer: Fixer) -> None:
        if not getattr(fixer, "id", None):
            logger.warning("Skipping fixer without an id: %r", fixer)
            return
        if fixer.id in self._fixers:
            logger.info("Overriding fixer %s", fixer.id)
        self._fixers[fixer.id] = fixer

    def _collect_from_module(self, module) -> int:
        loaded = 0
        for attr_name in ("FIXERS", "FIXER"):
            obj = getattr(module, attr_name, None)
            if obj is None:
                continue
            iterable: Iterable
            if isinstance(obj, (list, tuple)):
                iterable = obj
            else:
                iterable = [obj]
            for candidate in iterable:
                if isinstance(candidate, Fixer):
                    self._register(candidate)
                    loaded += 1
        return loaded


def build_default_registry(user_dir: Path | str | None = None) -> FixerRegistry:
    reg = FixerRegistry()
    reg.load_builtins()
    if user_dir:
        reg.load_user_dir(user_dir)
    return reg


__all__ = ["FixerRegistry", "build_default_registry"]
