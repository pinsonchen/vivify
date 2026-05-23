"""Cross-platform file lock for single-instance enforcement."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


class InstanceLock:
    """确保同一项目目录只运行一个 vivify 实例的跨平台文件锁。"""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """尝试获取锁。成功返回 True，已被其他进程持有返回 False。"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 写入 PID 以便调试
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, str(os.getpid()).encode())
            return True
        except (OSError, IOError):
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            return False

    def release(self) -> None:
        """释放锁并删除锁文件。"""
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> "InstanceLock":
        if not self.acquire():
            raise RuntimeError(
                f"Another vivify instance is already running for this project "
                f"(lock: {self.lock_path})"
            )
        return self

    def __exit__(self, *args) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()
