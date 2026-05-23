"""Daemon lifecycle management: start, stop, status."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from vivify.daemon.lock import InstanceLock


@dataclass
class DaemonStatus:
    """Daemon 运行状态。"""

    running: bool
    pid: Optional[int] = None
    repo_root: Optional[str] = None
    started_at: Optional[str] = None
    uptime_seconds: Optional[float] = None


# 全局实例注册表路径
_GLOBAL_REGISTRY = Path.home() / ".vivify" / "instances.json"


class DaemonManager:
    """管理 vivify daemon 的生命周期。"""

    def __init__(self, repo_root: Path, state_dir: Path):
        self.repo_root = repo_root.resolve()
        self.state_dir = (
            state_dir if state_dir.is_absolute() else (self.repo_root / state_dir)
        )
        self.pid_file = self.state_dir / "vivify.pid"
        self.lock_file = self.state_dir / "vivify.lock"
        self._lock = InstanceLock(self.lock_file)

    # --- 公共 API ---

    def start(self, *, extra_args: Optional[List[str]] = None) -> int:
        """后台启动 vivify daemon。返回子进程 PID。"""
        if self.is_running():
            status = self.status()
            raise RuntimeError(
                f"Vivify is already running (PID {status.pid}) for {self.repo_root}"
            )

        self.state_dir.mkdir(parents=True, exist_ok=True)

        # 构建子进程命令
        cmd: List[str] = [sys.executable, "-m", "vivify", "run"]
        if extra_args:
            cmd.extend(extra_args)

        # 加载全局环境配置
        custom_env = os.environ.copy()
        env_file = Path.home() / ".vivify" / "env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    custom_env[key.strip()] = value.strip()

        # 平台特定的后台启动
        if sys.platform == "win32":
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.repo_root),
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=custom_env,
            )
        else:
            # Unix: 使用 start_new_session 脱离终端，等效于 setsid
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=custom_env,
            )

        # 写入 PID 文件
        self.pid_file.write_text(str(proc.pid), encoding="utf-8")
        # 注册到全局实例表
        self._register_instance(proc.pid)

        return proc.pid

    def stop(self, *, force: bool = False, grace_seconds: int = 30) -> bool:
        """停止 daemon。返回 True 表示成功停止。"""
        pid = self._read_pid()
        if pid is None or not self._is_process_alive(pid):
            self._cleanup()
            return True

        if force:
            self._kill(pid, self._kill_signal())
        else:
            # 优雅停止
            self._kill(pid, signal.SIGTERM)
            deadline = time.time() + grace_seconds
            terminated = False
            while time.time() < deadline:
                if not self._is_process_alive(pid):
                    terminated = True
                    break
                time.sleep(0.5)
            if not terminated:
                # 超时强制杀死
                self._kill(pid, self._kill_signal())
                time.sleep(1)

        self._cleanup()
        return not self._is_process_alive(pid)

    def status(self) -> DaemonStatus:
        """获取当前 daemon 状态。"""
        pid = self._read_pid()
        if pid is None or not self._is_process_alive(pid):
            return DaemonStatus(running=False, repo_root=str(self.repo_root))

        # 从注册表获取启动时间
        started_at: Optional[str] = None
        uptime: Optional[float] = None
        for entry in self._load_registry():
            if entry.get("repo") == str(self.repo_root):
                started_at = entry.get("started_at")
                if started_at:
                    try:
                        start_dt = datetime.fromisoformat(started_at)
                        uptime = (
                            datetime.now(timezone.utc) - start_dt
                        ).total_seconds()
                    except ValueError:
                        uptime = None
                break

        return DaemonStatus(
            running=True,
            pid=pid,
            repo_root=str(self.repo_root),
            started_at=started_at,
            uptime_seconds=uptime,
        )

    def is_running(self) -> bool:
        """检查是否有实例正在运行。"""
        pid = self._read_pid()
        return pid is not None and self._is_process_alive(pid)

    # --- 全局实例注册表 ---

    @classmethod
    def list_instances(cls) -> List[DaemonStatus]:
        """列出本机所有运行中的 vivify 实例（自动清理已死实例）。"""
        registry = cls._load_registry_static()
        results: List[DaemonStatus] = []
        alive: List[dict] = []
        for entry in registry:
            pid = entry.get("pid")
            repo = entry.get("repo", "")
            started_at = entry.get("started_at")
            if pid and cls._is_process_alive_static(pid):
                uptime: Optional[float] = None
                if started_at:
                    try:
                        start_dt = datetime.fromisoformat(started_at)
                        uptime = (
                            datetime.now(timezone.utc) - start_dt
                        ).total_seconds()
                    except ValueError:
                        uptime = None
                results.append(
                    DaemonStatus(
                        running=True,
                        pid=pid,
                        repo_root=repo,
                        started_at=started_at,
                        uptime_seconds=uptime,
                    )
                )
                alive.append(entry)
        # 清理死掉的条目
        if len(alive) != len(registry):
            cls._save_registry_static(alive)
        return results

    def _register_instance(self, pid: int) -> None:
        registry = self._load_registry()
        # 移除同 repo 旧条目
        registry = [e for e in registry if e.get("repo") != str(self.repo_root)]
        registry.append(
            {
                "repo": str(self.repo_root),
                "pid": pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_registry(registry)

    def _unregister_instance(self) -> None:
        registry = self._load_registry()
        registry = [e for e in registry if e.get("repo") != str(self.repo_root)]
        self._save_registry(registry)

    def _cleanup(self) -> None:
        """清理 PID 文件和注册表条目。"""
        try:
            self.pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._unregister_instance()

    # --- 辅助方法 ---

    def _read_pid(self) -> Optional[int]:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _kill_signal() -> int:
        # Windows 没有 SIGKILL 等价物，统一用 SIGTERM
        return signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        return DaemonManager._is_process_alive_static(pid)

    @staticmethod
    def _is_process_alive_static(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # 进程存在但无权限发信号
            return True
        except OSError:
            return False

    @staticmethod
    def _kill(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass

    def _load_registry(self) -> list:
        return DaemonManager._load_registry_static()

    def _save_registry(self, data: list) -> None:
        DaemonManager._save_registry_static(data)

    @staticmethod
    def _load_registry_static() -> list:
        if not _GLOBAL_REGISTRY.exists():
            return []
        try:
            return json.loads(_GLOBAL_REGISTRY.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _save_registry_static(data: list) -> None:
        _GLOBAL_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        _GLOBAL_REGISTRY.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
