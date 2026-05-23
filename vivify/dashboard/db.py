"""Dashboard 只读数据库访问层。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class DashboardDB:
    """只读 SQLite 连接，用于 Dashboard 数据查询。"""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        self.conn = sqlite3.connect(
            str(db_path), check_same_thread=False, timeout=5
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self):
        self.conn.close()

    def get_status(self) -> dict:
        """获取最新的运行状态。"""
        row = self.conn.execute(
            "SELECT * FROM action_logs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"running": False, "last_action": None}
        return {
            "running": True,
            "last_action": dict(row),
            "run_id": row["run_id"],
        }

    def get_recent_actions(
        self, limit: int = 50, category: Optional[str] = None,
        action_type: Optional[str] = None, since: Optional[str] = None
    ) -> list[dict]:
        """查询操作日志。"""
        query = "SELECT * FROM action_logs WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_features(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        """查询特性请求。"""
        query = "SELECT * FROM feature_requests WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_feature(self, fid: int) -> Optional[dict]:
        """查询单个特性请求。"""
        row = self.conn.execute(
            "SELECT * FROM feature_requests WHERE id = ?", (fid,)
        ).fetchone()
        return dict(row) if row else None

    def get_kpi_snapshots(
        self, since: Optional[str] = None, source: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """查询 KPI 快照。"""
        query = "SELECT * FROM kpi_snapshots WHERE 1=1"
        params = []
        if since:
            query += " AND captured_at >= ?"
            params.append(since)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY captured_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_rounds(self, limit: int = 20) -> list[dict]:
        """查询运行轮次（通过 action_logs 聚合）。"""
        query = """
            SELECT run_id, round_num,
                   COUNT(*) as action_count,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success_count,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed_count,
                   MIN(created_at) as started_at,
                   MAX(created_at) as ended_at,
                   SUM(duration_seconds) as total_duration
            FROM action_logs
            GROUP BY run_id, round_num
            ORDER BY started_at DESC
            LIMIT ?
        """
        rows = self.conn.execute(query, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_failure_top(self, limit: int = 10) -> list[dict]:
        """查询失败次数最多的问题。"""
        rows = self.conn.execute(
            "SELECT * FROM failure_tracking ORDER BY fail_count DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_knowledge(
        self, category: Optional[str] = None, pattern: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """查询知识库。"""
        query = "SELECT * FROM knowledge_entries WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if pattern:
            query += " AND pattern LIKE ?"
            params.append(f"%{pattern}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
