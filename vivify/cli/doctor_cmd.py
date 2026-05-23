"""``vivify doctor`` — verify the host can run the kernel."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from vivify.config.loader import load_config


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("doctor", help="Verify environment + config sanity.")
    p.set_defaults(func=run)


def _check_binary(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if not path:
        return False, "missing on PATH"
    try:
        res = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        ver = (res.stdout or res.stderr or "").strip().splitlines()[0:1]
        return True, " ".join(ver)
    except Exception as e:  # pragma: no cover
        return False, f"--version failed: {e}"


def run(args: argparse.Namespace) -> int:
    cfg_path = getattr(args, "config", None)
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print(f"[FAIL] config: {e}")
        return 2

    print("[ OK ] config loaded:", cfg_path or ".vivify.yml")

    overall_ok = True
    for binary in ("git", "gh", cfg.agent.qodercli.binary_path):
        ok, info = _check_binary(binary)
        prefix = "[ OK ]" if ok else "[WARN]"
        if not ok:
            overall_ok = False
        print(f"{prefix} {binary}: {info}")

    token_env = cfg.github.token_env
    has_token = bool(os.environ.get(token_env))
    print(f"[{ 'OK' if has_token else 'WARN'}] env {token_env}: "
          f"{'present' if has_token else 'missing (gh may still work via gh auth)'}")

    # 检查 ~/.vivify/env 文件
    env_file = Path.home() / ".vivify" / "env"
    if env_file.exists():
        print("[ OK ] ~/.vivify/env: exists (will be loaded by daemon)")
    elif not has_token:
        print("[WARN] ~/.vivify/env: missing (run 'vivify init' to configure)")

    # 验证 gh 实际认证状态
    try:
        gh_result = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10
        )
        gh_authed = gh_result.returncode == 0
        print(f"[{'OK' if gh_authed else 'WARN'}] gh auth: "
              f"{'authenticated' if gh_authed else 'not authenticated'}")
        if not gh_authed and not has_token:
            overall_ok = False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    state_dir = Path(cfg.state_dir)
    if not state_dir.exists():
        print(f"[INFO] state dir `{state_dir}` will be created on first run")
    else:
        print(f"[ OK ] state dir `{state_dir}` exists")

    print()
    if overall_ok and has_token:
        print("doctor: OK")
        return 0
    print("doctor: warnings present — see above")
    return 1


__all__ = ["register", "run"]
