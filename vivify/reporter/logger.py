"""Logging utilities — set up rotating file + console handlers for vivify.

A single ``setup_logging`` call configures the ``vivify`` logger tree so
all kernel/probe/fixer modules end up writing to the same place. The logger
reporter sits on top of this and re-emits :class:`ActionLog` rows as
human-readable INFO entries.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from vivify.interfaces.reporter import Reporter
from vivify.models.snapshot import ActionLog

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(
    *,
    log_dir: Path | str = ".vivify/logs",
    level: int | str = "INFO",
    console: bool = True,
    file_name: str = "vivify.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> Path:
    """Configure the ``vivify`` logger tree. Returns the active log file path."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_path = log_path / file_name

    root = logging.getLogger("vivify")
    root.setLevel(level)

    # Don't duplicate handlers when called twice (tests, daemon reload).
    if not any(getattr(h, "_vivify_tag", False) for h in root.handlers):
        formatter = logging.Formatter(_DEFAULT_FORMAT)

        fh = logging.handlers.RotatingFileHandler(
            file_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        fh.setFormatter(formatter)
        fh._vivify_tag = True  # type: ignore[attr-defined]
        root.addHandler(fh)

        if console:
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            ch._vivify_tag = True  # type: ignore[attr-defined]
            root.addHandler(ch)

    return file_path


class LoggerReporter(Reporter):
    """Mirror :class:`ActionLog` records to the standard vivify logger."""

    def __init__(self, *, logger_name: str = "vivify.reporter"):
        self.logger = logging.getLogger(logger_name)

    def report(self, action: ActionLog) -> None:
        try:
            msg = (
                f"[{action.action_type}/{action.status}] "
                f"{action.category or '-'}/{action.title or '-'}"
            )
            extra = []
            if action.duration_seconds is not None:
                extra.append(f"{action.duration_seconds:.1f}s")
            if action.pr_url:
                extra.append(action.pr_url)
            if extra:
                msg += " (" + ", ".join(extra) + ")"
            self.logger.info(msg)
        except Exception as e:  # pragma: no cover — must never raise
            self.logger.debug("LoggerReporter swallowed: %s", e)


__all__ = ["LoggerReporter", "setup_logging"]
