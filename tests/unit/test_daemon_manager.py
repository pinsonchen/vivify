"""Tests for vivify.daemon module."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vivify.daemon.lock import InstanceLock
from vivify.daemon.manager import DaemonManager


class TestInstanceLock:
    """Test InstanceLock acquire/release semantics."""

    def test_acquire_and_release(self, tmp_path: Path) -> None:
        lock = InstanceLock(tmp_path / "test.lock")
        assert lock.acquire() is True
        # Lock file should contain PID
        content = (tmp_path / "test.lock").read_text()
        assert content == str(os.getpid())
        lock.release()
        assert not (tmp_path / "test.lock").exists()

    def test_double_acquire_fails(self, tmp_path: Path) -> None:
        lock1 = InstanceLock(tmp_path / "test.lock")
        lock2 = InstanceLock(tmp_path / "test.lock")
        assert lock1.acquire() is True
        assert lock2.acquire() is False
        lock1.release()

    def test_acquire_after_release(self, tmp_path: Path) -> None:
        lock = InstanceLock(tmp_path / "test.lock")
        assert lock.acquire() is True
        lock.release()
        assert lock.acquire() is True
        lock.release()

    def test_context_manager(self, tmp_path: Path) -> None:
        lock = InstanceLock(tmp_path / "test.lock")
        with lock:
            assert (tmp_path / "test.lock").exists()
        assert not (tmp_path / "test.lock").exists()

    def test_context_manager_raises_if_locked(self, tmp_path: Path) -> None:
        lock1 = InstanceLock(tmp_path / "test.lock")
        lock1.acquire()
        lock2 = InstanceLock(tmp_path / "test.lock")
        with pytest.raises(RuntimeError, match="already running"):
            with lock2:
                pass
        lock1.release()


class TestDaemonManager:
    """Test DaemonManager lifecycle operations."""

    def test_not_running_initially(self, tmp_path: Path) -> None:
        mgr = DaemonManager(repo_root=tmp_path, state_dir=tmp_path / ".vivify")
        assert mgr.is_running() is False

    def test_status_when_not_running(self, tmp_path: Path) -> None:
        mgr = DaemonManager(repo_root=tmp_path, state_dir=tmp_path / ".vivify")
        status = mgr.status()
        assert status.running is False
        assert status.pid is None

    def test_pid_file_with_dead_process(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".vivify"
        state_dir.mkdir(parents=True)
        pid_file = state_dir / "vivify.pid"
        pid_file.write_text("999999", encoding="utf-8")  # unlikely to be alive
        mgr = DaemonManager(repo_root=tmp_path, state_dir=state_dir)
        assert mgr.is_running() is False

    def test_stop_when_not_running(self, tmp_path: Path) -> None:
        mgr = DaemonManager(repo_root=tmp_path, state_dir=tmp_path / ".vivify")
        assert mgr.stop() is True

    def test_list_instances_empty(self) -> None:
        with patch.object(DaemonManager, '_load_registry_static', return_value=[]):
            instances = DaemonManager.list_instances()
            assert instances == []

    def test_list_instances_filters_dead(self) -> None:
        fake_registry = [
            {"repo": "/tmp/dead-project", "pid": 999999, "started_at": "2025-01-01T00:00:00+00:00"}
        ]
        with patch.object(DaemonManager, '_load_registry_static', return_value=fake_registry):
            with patch.object(DaemonManager, '_save_registry_static'):
                instances = DaemonManager.list_instances()
                assert len(instances) == 0
