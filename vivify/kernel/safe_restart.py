"""Graceful restart helpers for the long-running daemon.

When the kernel detects its own code changed (via :func:`code_hash.compute_code_hash`),
it should finish in-flight work and re-exec itself so the new code is loaded.
Inspired by ``main.py::_safe_restart`` from the channels-monitor source — but
project-agnostic.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def safe_restart(
    *,
    drain: Optional[Callable[[], None]] = None,
    grace_seconds: float = 5.0,
    reason: str = "code change detected",
) -> None:
    """Drain in-flight work, then ``os.execv`` the current process.

    On systemd / Docker the supervisor will restart us automatically if execv
    fails for some reason — we still log so the operator notices.
    """
    logger.warning("safe_restart requested: %s", reason)
    if drain is not None:
        try:
            drain()
        except Exception as e:  # pragma: no cover — best-effort cleanup
            logger.warning("drain hook raised: %s", e)
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as e:  # pragma: no cover
        logger.error("os.execv failed: %s — exiting so supervisor can restart", e)
        os._exit(75)


__all__ = ["safe_restart"]
