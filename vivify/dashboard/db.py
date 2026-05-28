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

    def get_recent_features(self, limit: int = 10) -> list[dict]:
        """获取最近更新的特性。"""
        rows = self.conn.execute(
            "SELECT id, title, status, priority, type, updated_at"
            " FROM feature_requests ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_feature_stats(self) -> dict:
        """查询特性统计信息：按类型、优先级、状态分布，retry 数量和总数。"""
        type_rows = self.conn.execute(
            "SELECT COALESCE(type,'unknown') as t, COUNT(*) as cnt FROM feature_requests GROUP BY type"
        ).fetchall()
        by_type = {r["t"]: r["cnt"] for r in type_rows}

        priority_rows = self.conn.execute(
            "SELECT COALESCE(priority,'unknown') as p, COUNT(*) as cnt"
            " FROM feature_requests GROUP BY priority"
        ).fetchall()
        by_priority = {r["p"]: r["cnt"] for r in priority_rows}

        status_rows = self.conn.execute(
            "SELECT COALESCE(status,'unknown') as s, COUNT(*) as cnt"
            " FROM feature_requests GROUP BY status"
        ).fetchall()
        by_status = {r["s"]: r["cnt"] for r in status_rows}

        retried_count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM feature_requests WHERE retry_count > 0"
        ).fetchone()["cnt"]

        total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM feature_requests"
        ).fetchone()["cnt"]

        return {
            "total": total,
            "by_type": by_type,
            "by_priority": by_priority,
            "by_status": by_status,
            "retried_count": retried_count,
        }

    def get_features_timeline(self, days: int = 30) -> list[dict]:
        """按天聚合 feature 生命周期事件，作为趋势 Tab 的备选数据源。

        返回近 ``days`` 天内每一天的：created / verified / deployed_with_issues / rejected 计数。
        所有查询均允许字段为 NULL、表为空 —— 并返回空列表。
        """
        # 在 SQLite 中用 substr 取前 10 位作为日期 key（ISO 8601 保证字序可比）。
        try:
            rows = self.conn.execute(
                f"""
                WITH events AS (
                    SELECT substr(created_at, 1, 10) AS day, 'created' AS kind FROM feature_requests WHERE created_at IS NOT NULL
                    UNION ALL
                    SELECT substr(verified_at, 1, 10), 'verified' FROM feature_requests WHERE verified_at IS NOT NULL
                    UNION ALL
                    SELECT substr(updated_at, 1, 10), 'rejected' FROM feature_requests WHERE status = 'rejected' AND updated_at IS NOT NULL
                    UNION ALL
                    SELECT substr(updated_at, 1, 10), 'deployed_with_issues' FROM feature_requests WHERE status = 'deployed_with_issues' AND updated_at IS NOT NULL
                )
                SELECT day, kind, COUNT(*) AS cnt
                FROM events
                WHERE day IS NOT NULL AND day != ''
                  AND day >= date('now', '-' || ? || ' days')
                GROUP BY day, kind
                ORDER BY day ASC
                """,
                (int(days),),
            ).fetchall()
        except sqlite3.Error:
            return []

        # 按 day 合并为一行
        bucket: dict[str, dict] = {}
        for r in rows:
            day = r["day"]
            if not day:
                continue
            entry = bucket.setdefault(
                day,
                {"day": day, "created": 0, "verified": 0, "rejected": 0, "deployed_with_issues": 0},
            )
            kind = r["kind"]
            if kind in entry:
                entry[kind] = int(r["cnt"])
        return [bucket[k] for k in sorted(bucket.keys())]

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

    def get_alerts(self, days: int = 7) -> list[dict]:
        """获取近 N 天的告警事件：失败/异常的 features + 失败的 actions。"""
        alerts: list[dict] = []
        try:
            # 1. 状态异常的 features（deployed_with_issues / rejected）
            feature_rows = self.conn.execute(
                """
                SELECT id, title, status, priority, type, retry_count, updated_at
                FROM feature_requests
                WHERE status IN ('deployed_with_issues', 'rejected')
                  AND updated_at >= datetime('now', '-' || ? || ' days')
                ORDER BY updated_at DESC
                """,
                (int(days),),
            ).fetchall()
            for r in feature_rows:
                severity = 'high' if r['status'] == 'deployed_with_issues' else 'medium'
                alerts.append({
                    'type': 'feature',
                    'severity': severity,
                    'feature_id': r['id'],
                    'title': r['title'],
                    'status': r['status'],
                    'priority': r['priority'],
                    'feature_type': r['type'],
                    'retry_count': r['retry_count'] or 0,
                    'time': r['updated_at'],
                })
        except Exception:
            pass

        try:
            # 2. 失败的 action_logs
            action_rows = self.conn.execute(
                """
                SELECT id, action_type, category, title, result_summary, created_at
                FROM action_logs
                WHERE status = 'failed'
                  AND created_at >= datetime('now', '-' || ? || ' days')
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (int(days),),
            ).fetchall()
            for r in action_rows:
                alerts.append({
                    'type': 'action_failure',
                    'severity': 'medium',
                    'action_id': r['id'],
                    'title': r['title'] or f"{r['action_type']} 失败",
                    'category': r['category'],
                    'action_type': r['action_type'],
                    'summary': r['result_summary'],
                    'time': r['created_at'],
                })
        except Exception:
            pass

        # 按时间倒序合并
        alerts.sort(key=lambda x: x.get('time') or '', reverse=True)
        return alerts


# ─── 写操作函数（独立连接，不受 query_only 限制） ───


def update_feature_status(
    db_path: Path, feature_id: int, new_status: str, retry_increment: bool = False
) -> bool:
    """更新 feature 状态（写操作，使用独立的可写连接）。"""
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        if retry_increment:
            conn.execute(
                "UPDATE feature_requests SET status = ?, retry_count = retry_count + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (new_status, feature_id),
            )
        else:
            conn.execute(
                "UPDATE feature_requests SET status = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (new_status, feature_id),
            )
        conn.commit()
        return conn.total_changes > 0
    except Exception:
        return False
    finally:
        conn.close()


def log_manual_action(db_path: Path, feature_id: int, action: str) -> None:
    """记录手动操作日志到 action_logs。"""
    import uuid
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute(
            "INSERT INTO action_logs (run_id, round_num, action_type, status, category, title, result_summary, duration_seconds) "
            "VALUES (?, 0, ?, 'success', 'manual', ?, ?, 0)",
            (
                f"manual-{uuid.uuid4().hex[:8]}",
                f"manual_{action}",
                f"手动{action} feature #{feature_id}",
                f"用户通过 Dashboard 手动执行 {action} 操作",
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
