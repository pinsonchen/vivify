"""Vivify Dashboard FastAPI 应用。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import DashboardDB
from .log_streamer import tail_log


def create_app(state_dir: Optional[Path] = None) -> FastAPI:
    """创建 Dashboard FastAPI 应用实例。"""
    app = FastAPI(title="Vivify Dashboard", version="0.1.0")
    
    # 解析状态目录
    if state_dir is None:
        state_dir = Path.cwd() / ".vivify"
    
    db_path = state_dir / "state.db"
    log_path = state_dir / "logs" / "vivify.log"
    static_dir = Path(__file__).parent / "static"

    # 延迟初始化 DB（允许 DB 文件尚不存在时也能启动）
    _db: Optional[DashboardDB] = None

    def get_db() -> Optional[DashboardDB]:
        nonlocal _db
        if _db is None and db_path.exists():
            _db = DashboardDB(db_path)
        return _db

    # --- API 端点 ---

    @app.get("/api/status")
    async def api_status():
        """获取 daemon 运行状态和最新轮次信息。"""
        db = get_db()
        if not db:
            return {"running": False, "message": "数据库尚未创建，请先运行 vivify run"}
        
        # 读取 PID 文件判断 daemon 状态
        pid_file = state_dir / "vivify.pid"
        daemon_running = False
        daemon_pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                import os
                os.kill(pid, 0)  # 检测进程是否存活
                daemon_running = True
                daemon_pid = pid
            except (ValueError, OSError):
                pass
        
        status = db.get_status()
        rounds = db.get_rounds(limit=1)
        return {
            "daemon_running": daemon_running,
            "daemon_pid": daemon_pid,
            "last_action": status.get("last_action"),
            "latest_round": rounds[0] if rounds else None,
        }

    @app.get("/api/actions")
    async def api_actions(
        limit: int = Query(50, le=200),
        category: Optional[str] = None,
        action_type: Optional[str] = None,
        since: Optional[str] = None,
    ):
        db = get_db()
        if not db:
            return []
        return db.get_recent_actions(limit=limit, category=category, action_type=action_type, since=since)

    @app.get("/api/features")
    async def api_features(
        status: Optional[str] = None,
        limit: int = Query(50, le=200),
    ):
        db = get_db()
        if not db:
            return []
        return db.get_features(status=status, limit=limit)

    @app.get("/api/features/{fid}")
    async def api_feature_detail(fid: int):
        db = get_db()
        if not db:
            return {"error": "数据库未就绪"}
        feature = db.get_feature(fid)
        if not feature:
            return {"error": "特性请求不存在"}
        return feature

    @app.get("/api/kpi/snapshots")
    async def api_kpi_snapshots(
        since: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = Query(100, le=500),
    ):
        db = get_db()
        if not db:
            return []
        return db.get_kpi_snapshots(since=since, source=source, limit=limit)

    @app.get("/api/rounds")
    async def api_rounds(limit: int = Query(20, le=100)):
        db = get_db()
        if not db:
            return []
        return db.get_rounds(limit=limit)

    @app.get("/api/rounds/latest")
    async def api_latest_round():
        db = get_db()
        if not db:
            return None
        rounds = db.get_rounds(limit=1)
        return rounds[0] if rounds else None

    @app.get("/api/failures")
    async def api_failures(limit: int = Query(10, le=50)):
        db = get_db()
        if not db:
            return []
        return db.get_failure_top(limit=limit)

    @app.get("/api/knowledge")
    async def api_knowledge(
        category: Optional[str] = None,
        pattern: Optional[str] = None,
        limit: int = Query(50, le=200),
    ):
        db = get_db()
        if not db:
            return []
        return db.get_knowledge(category=category, pattern=pattern, limit=limit)

    @app.get("/api/logs/stream")
    async def api_log_stream():
        """SSE 实时日志流。"""
        return StreamingResponse(
            tail_log(log_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- 前端静态文件 ---
    
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>Vivify Dashboard</h1><p>静态文件未找到</p>"

    return app
