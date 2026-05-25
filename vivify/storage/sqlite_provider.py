"""SQLite-backed :class:`StorageProvider` implementation.

This is the default storage backend wired up by ``vivify init``. It keeps
all state inside a single ``.vivify/state.db`` file using WAL mode for
concurrent reads. Schema lives under ``vivify/storage/migrations/`` and is
applied on :meth:`initialize`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from vivify.interfaces.storage import StorageProvider
from vivify.models.feature import FeatureRequest, FeatureStatus
from vivify.models.snapshot import ActionLog, KnowledgeEntry, KpiSnapshot

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────
def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # Accept both with and without microseconds, with or without trailing Z.
    s = value.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _bool_to_int(b: Any) -> int:
    return 1 if b else 0


# ── provider ──────────────────────────────────────────────────────────────────
class SqliteStorageProvider(StorageProvider):
    """Threadsafe SQLite implementation of :class:`StorageProvider`.

    A single connection is opened per instance with ``check_same_thread=False``
    and guarded by a re-entrant lock; this is sufficient for the kernel's
    workload (low write QPS, sequential within each round).
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # ── lifecycle ──
    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly when needed
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._run_migrations()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── migrations ──
    def _run_migrations(self) -> None:
        assert self._conn is not None
        applied = self._applied_migration_versions()
        migration_files = sorted(
            f
            for f in resources.files("vivify.storage.migrations").iterdir()
            if f.name.endswith(".sql")
        )
        for entry in migration_files:
            version = int(entry.name.split("_", 1)[0])
            if version in applied:
                continue
            sql = entry.read_text(encoding="utf-8")
            logger.info("Applying SQLite migration %s", entry.name)
            with self._lock, self._conn:
                self._conn.executescript(sql)

    def _applied_migration_versions(self) -> set[int]:
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'"
        )
        if cur.fetchone() is None:
            return set()
        rows = self._conn.execute("SELECT version FROM _schema_migrations").fetchall()
        return {int(r["version"]) for r in rows}

    # ── feature requests ──
    def create_feature(self, fr: FeatureRequest) -> int:
        with self._guarded() as conn:
            cur = conn.execute(
                """
                INSERT INTO feature_requests (
                    title, description, type, parent_goal, parent_id, priority,
                    status, development_result, commit_hash, pr_url,
                    feasibility, summary, verification_method, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fr.title, fr.description, fr.type, fr.parent_goal, fr.parent_id,
                    fr.priority, fr.status, fr.development_result, fr.commit_hash,
                    fr.pr_url, fr.feasibility, fr.summary, fr.verification_method,
                    _to_iso(fr.created_at) or _to_iso(datetime.now(timezone.utc)),
                    _to_iso(fr.updated_at) or _to_iso(datetime.now(timezone.utc)),
                ),
            )
            fid = int(cur.lastrowid or 0)
            fr.id = fid
            return fid

    def get_feature(self, fid: int) -> Optional[FeatureRequest]:
        with self._guarded() as conn:
            row = conn.execute(
                "SELECT * FROM feature_requests WHERE id = ?", (fid,)
            ).fetchone()
        return self._row_to_feature(row) if row else None

    def list_features(
        self,
        status: Optional[FeatureStatus] = None,
        limit: int = 200,
    ) -> list[FeatureRequest]:
        sql = "SELECT * FROM feature_requests"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY id DESC LIMIT ?"
        params = (*params, int(limit))
        with self._guarded() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_feature(r) for r in rows if r]

    def update_feature(self, fid: int, **fields) -> None:
        if not fields:
            return
        allowed = {
            "title", "description", "type", "parent_goal", "parent_id",
            "priority", "status", "development_result", "commit_hash",
            "pr_url", "feasibility", "summary", "verification_method",
        }
        sets: list[str] = []
        values: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"update_feature: column not allowed: {k}")
            sets.append(f"{k} = ?")
            values.append(v)
        sets.append("updated_at = ?")
        values.append(_to_iso(datetime.now(timezone.utc)))
        values.append(fid)
        with self._guarded() as conn:
            conn.execute(
                f"UPDATE feature_requests SET {', '.join(sets)} WHERE id = ?",
                tuple(values),
            )

    @staticmethod
    def _row_to_feature(row: sqlite3.Row) -> FeatureRequest:
        # verification_method may not exist in older databases before migration 0002
        try:
            verification_method = row["verification_method"]
        except (IndexError, KeyError):
            verification_method = None
        return FeatureRequest(
            id=int(row["id"]),
            title=row["title"],
            description=row["description"] or "",
            type=row["type"] or "feature",
            parent_goal=row["parent_goal"],
            parent_id=row["parent_id"],
            priority=row["priority"],
            verification_method=verification_method,
            status=row["status"] or "pending",
            development_result=row["development_result"] or "",
            commit_hash=row["commit_hash"],
            pr_url=row["pr_url"],
            feasibility=row["feasibility"] or "",
            summary=row["summary"] or "",
            created_at=_from_iso(row["created_at"]) or datetime.now(timezone.utc),
            updated_at=_from_iso(row["updated_at"]) or datetime.now(timezone.utc),
        )

    # ── action logs ──
    def log_action(self, record: ActionLog) -> int:
        with self._guarded() as conn:
            cur = conn.execute(
                """
                INSERT INTO action_logs (
                    run_id, round_num, action_type, status, category, level, title,
                    prompt, result_summary, improved, duration_seconds, details_json,
                    commit_hash, pr_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id, int(record.round_num), record.action_type,
                    record.status, record.category, record.level, record.title,
                    record.prompt, record.result_summary, _bool_to_int(record.improved),
                    record.duration_seconds,
                    json.dumps(record.details, ensure_ascii=False, default=str),
                    record.commit_hash, record.pr_url,
                    _to_iso(record.created_at) or _to_iso(datetime.now(timezone.utc)),
                ),
            )
            lid = int(cur.lastrowid or 0)
            record.id = lid
            return lid

    def search_action_logs(
        self,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[ActionLog]:
        sql = "SELECT * FROM action_logs"
        params: tuple[Any, ...] = ()
        if category:
            sql += " WHERE category = ?"
            params = (category,)
        sql += " ORDER BY id DESC LIMIT ?"
        params = (*params, int(limit))
        with self._guarded() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[ActionLog] = []
        for r in rows:
            out.append(
                ActionLog(
                    id=int(r["id"]),
                    run_id=r["run_id"] or "",
                    round_num=int(r["round_num"] or 0),
                    action_type=r["action_type"] or "",
                    status=r["status"] or "running",
                    category=r["category"],
                    level=r["level"],
                    title=r["title"],
                    prompt=r["prompt"],
                    result_summary=r["result_summary"],
                    improved=bool(r["improved"]),
                    duration_seconds=r["duration_seconds"],
                    details=_safe_loads(r["details_json"]),
                    commit_hash=r["commit_hash"],
                    pr_url=r["pr_url"],
                    created_at=_from_iso(r["created_at"]) or datetime.now(timezone.utc),
                )
            )
        return out

    # ── failure tracking ──
    def record_failure(self, problem_hash: str, category: str, title: str) -> int:
        now = _to_iso(datetime.now(timezone.utc))
        with self._guarded() as conn:
            cur = conn.execute(
                "SELECT fail_count FROM failure_tracking WHERE problem_hash = ?",
                (problem_hash,),
            )
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO failure_tracking
                        (problem_hash, category, title, fail_count, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (problem_hash, category, title, now, now),
                )
                return 1
            new_count = int(row["fail_count"]) + 1
            conn.execute(
                "UPDATE failure_tracking SET fail_count = ?, title = ?, category = ?, last_seen_at = ? WHERE problem_hash = ?",
                (new_count, title, category, now, problem_hash),
            )
            return new_count

    def reset_failure(self, problem_hash: str) -> None:
        with self._guarded() as conn:
            conn.execute(
                "UPDATE failure_tracking SET fail_count = 0, upgraded_feature_id = NULL WHERE problem_hash = ?",
                (problem_hash,),
            )

    def mark_upgraded(self, problem_hash: str, feature_id: int) -> None:
        with self._guarded() as conn:
            conn.execute(
                "UPDATE failure_tracking SET upgraded_feature_id = ? WHERE problem_hash = ?",
                (int(feature_id), problem_hash),
            )

    def get_failure_count(self, problem_hash: str) -> int:
        with self._guarded() as conn:
            row = conn.execute(
                "SELECT fail_count FROM failure_tracking WHERE problem_hash = ?",
                (problem_hash,),
            ).fetchone()
        return int(row["fail_count"]) if row else 0

    def get_upgraded_feature_id(self, problem_hash: str) -> Optional[int]:
        with self._guarded() as conn:
            row = conn.execute(
                "SELECT upgraded_feature_id FROM failure_tracking WHERE problem_hash = ?",
                (problem_hash,),
            ).fetchone()
        if not row or row["upgraded_feature_id"] is None:
            return None
        return int(row["upgraded_feature_id"])

    # ── knowledge ──
    def add_knowledge(self, k: KnowledgeEntry) -> int:
        with self._guarded() as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_entries
                    (category, pattern, solution_summary, success, feature_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    k.category, k.pattern, k.solution_summary,
                    _bool_to_int(k.success), k.feature_id,
                    _to_iso(k.created_at) or _to_iso(datetime.now(timezone.utc)),
                ),
            )
            kid = int(cur.lastrowid or 0)
            k.id = kid
            return kid

    def search_knowledge(
        self, category: str, pattern: str, limit: int = 5
    ) -> list[KnowledgeEntry]:
        like = f"%{pattern}%" if pattern else "%"
        with self._guarded() as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_entries
                 WHERE category = ? AND pattern LIKE ?
                 ORDER BY id DESC LIMIT ?
                """,
                (category, like, int(limit)),
            ).fetchall()
        return [
            KnowledgeEntry(
                id=int(r["id"]),
                category=r["category"],
                pattern=r["pattern"],
                solution_summary=r["solution_summary"] or "",
                success=bool(r["success"]),
                feature_id=r["feature_id"],
                created_at=_from_iso(r["created_at"]) or datetime.now(timezone.utc),
            )
            for r in rows
        ]

    # ── KPI snapshots ──
    def write_snapshot(self, snap: KpiSnapshot) -> int:
        with self._guarded() as conn:
            cur = conn.execute(
                """
                INSERT INTO kpi_snapshots (source, metrics_json, overall_score, grade, captured_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snap.source,
                    json.dumps(snap.metrics, ensure_ascii=False, default=str),
                    snap.overall_score,
                    snap.grade,
                    _to_iso(snap.captured_at) or _to_iso(datetime.now(timezone.utc)),
                ),
            )
            sid = int(cur.lastrowid or 0)
            snap.id = sid
            return sid

    def read_snapshots(self, since: datetime) -> list[KpiSnapshot]:
        since_iso = _to_iso(since)
        with self._guarded() as conn:
            rows = conn.execute(
                "SELECT * FROM kpi_snapshots WHERE captured_at >= ? ORDER BY captured_at ASC",
                (since_iso,),
            ).fetchall()
        return [
            KpiSnapshot(
                id=int(r["id"]),
                source=r["source"],
                metrics=_safe_loads(r["metrics_json"]),
                overall_score=r["overall_score"],
                grade=r["grade"],
                captured_at=_from_iso(r["captured_at"]) or datetime.now(timezone.utc),
            )
            for r in rows
        ]

    # ── internals ──
    def _guarded(self) -> "_GuardedConnection":
        if self._conn is None:
            raise RuntimeError("SqliteStorageProvider.initialize() must be called first")
        return _GuardedConnection(self._conn, self._lock)


class _GuardedConnection:
    """Context manager: holds the lock for the duration of a transaction."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release()


def _safe_loads(payload: Any) -> dict:
    if not payload:
        return {}
    if isinstance(payload, dict):
        return payload
    try:
        loaded = json.loads(payload)
        return loaded if isinstance(loaded, dict) else {"_raw": loaded}
    except (TypeError, ValueError):
        return {"_raw": str(payload)}


__all__ = ["SqliteStorageProvider"]
