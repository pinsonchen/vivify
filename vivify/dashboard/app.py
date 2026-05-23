"""Vivify Dashboard FastAPI 应用。"""
from __future__ import annotations

import base64
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import DashboardDB
from .log_streamer import tail_log

# 全局实例注册表路径
INSTANCES_FILE = Path.home() / ".vivify" / "instances.json"


# ────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────


def _encode_instance_id(repo_path: str) -> str:
    """将 repo 路径编码为 URL-safe instance_id。"""
    return base64.urlsafe_b64encode(repo_path.encode()).decode().rstrip("=")


def _decode_instance_id(instance_id: str) -> str:
    """解码 instance_id 为 repo 路径。"""
    padding = 4 - len(instance_id) % 4
    if padding != 4:
        instance_id += "=" * padding
    return base64.urlsafe_b64decode(instance_id.encode()).decode()


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_vivify_config(repo_path: Path) -> dict:
    """读取项目的 .vivify.yml 配置。"""
    config_file = repo_path / ".vivify.yml"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _parse_goals(repo_path: Path) -> list:
    """从 GOALS.md 提取目标标题列表。"""
    goals_file = repo_path / "GOALS.md"
    if not goals_file.exists():
        return []
    try:
        content = goals_file.read_text(encoding="utf-8")
    except Exception:
        return []
    goals = []
    for line in content.splitlines():
        if line.startswith("## Goal:"):
            goals.append(line.replace("## Goal:", "").strip())
    return goals


def _parse_goals_detailed(repo_path: Path) -> tuple[str, list]:
    """从 GOALS.md 提取完整内容和结构化目标列表。"""
    goals_file = repo_path / "GOALS.md"
    if not goals_file.exists():
        return "", []
    try:
        content = goals_file.read_text(encoding="utf-8")
    except Exception:
        return "", []
    goals_list = []
    current_goal = None
    for line in content.splitlines():
        if line.startswith("## Goal:"):
            if current_goal:
                goals_list.append(current_goal)
            current_goal = {"title": line.replace("## Goal:", "").strip(), "kpis": [], "deadline": ""}
        elif current_goal:
            stripped = line.strip()
            if stripped.startswith("- KPI:") or stripped.startswith("- kpi:"):
                current_goal["kpis"].append(stripped.replace("- KPI:", "").replace("- kpi:", "").strip())
            elif stripped.lower().startswith("deadline:"):
                current_goal["deadline"] = stripped.split(":", 1)[1].strip()
    if current_goal:
        goals_list.append(current_goal)
    return content, goals_list


def _check_config_health(repo_root: Path, state_dir_path: Path) -> dict:
    """检查项目配置完整性，返回结构化检查结果和修复建议。"""
    checks = []

    # 1. 配置文件存在性
    config_path = repo_root / ".vivify.yml"
    if config_path.exists():
        checks.append({
            "id": "config_file", "name": "配置文件", "category": "基础",
            "status": "ok", "message": ".vivify.yml 已存在",
        })
        cfg = _read_vivify_config(repo_root)
    else:
        checks.append({
            "id": "config_file", "name": "配置文件", "category": "基础",
            "status": "missing", "message": ".vivify.yml 不存在",
            "fix_hint": "运行 vivify init 初始化项目",
        })
        cfg = {}

    project = cfg.get("project", {})

    # 2. 项目名称
    if project.get("name"):
        checks.append({
            "id": "project_name", "name": "项目名称", "category": "基础",
            "status": "ok", "message": project["name"],
        })
    else:
        checks.append({
            "id": "project_name", "name": "项目名称", "category": "基础",
            "status": "missing", "message": "未设置项目名称",
            "fix_hint": "在 .vivify.yml 中设置 project.name",
        })

    # 3. 项目类型/场景
    if project.get("type"):
        checks.append({
            "id": "project_type", "name": "场景类型", "category": "基础",
            "status": "ok", "message": project["type"],
        })
    else:
        checks.append({
            "id": "project_type", "name": "场景类型", "category": "基础",
            "status": "missing", "message": "未设置场景类型",
            "fix_hint": "运行 vivify init 或设置 project.type",
        })

    # 4. qodercli 可用性
    qodercli_bin = cfg.get("agent", {}).get("qodercli", {}).get("binary_path", "qodercli")
    qodercli_found = shutil.which(qodercli_bin)
    if qodercli_found:
        checks.append({
            "id": "qodercli", "name": "AI 智能引擎 (qodercli)", "category": "智能",
            "status": "ok", "message": f"已就绪: {qodercli_found}",
        })
    else:
        checks.append({
            "id": "qodercli", "name": "AI 智能引擎 (qodercli)", "category": "智能",
            "status": "missing", "message": "qodercli 未找到",
            "fix_hint": "安装 qodercli 并确保在 PATH 中",
        })

    # 5. GH_TOKEN
    env_file = Path.home() / ".vivify" / "env"
    has_token_env = bool(os.environ.get("GH_TOKEN", ""))
    has_token_file = False
    if env_file.exists():
        try:
            _env_content = env_file.read_text(encoding="utf-8")
            _parts = _env_content.split("GH_TOKEN=")
            has_token_file = len(_parts) > 1 and len(_parts[1].split("\n")[0].strip()) > 0
        except OSError:
            pass
    if has_token_env or has_token_file:
        checks.append({
            "id": "gh_token", "name": "GitHub 认证", "category": "集成",
            "status": "ok", "message": "GH_TOKEN 已配置",
        })
    else:
        checks.append({
            "id": "gh_token", "name": "GitHub 认证", "category": "集成",
            "status": "missing", "message": "GH_TOKEN 未配置，无法创建 PR",
            "fix_hint": "运行 vivify init 配置 token，或编辑 ~/.vivify/env",
        })

    # 6. Deploy 配置
    deploy = cfg.get("deploy", {})
    deploy_method = deploy.get("method", project.get("deploy_method", "manual"))
    if deploy_method and deploy_method != "manual":
        deploy_detail = f"方式: {deploy_method}"
        if deploy.get("ssh_host"):
            deploy_detail += f" → {deploy['ssh_host']}"
        checks.append({
            "id": "deploy_method", "name": "部署方式", "category": "部署",
            "status": "ok", "message": deploy_detail,
        })
    else:
        checks.append({
            "id": "deploy_method", "name": "部署方式", "category": "部署",
            "status": "warning", "message": "部署方式为 manual，无法自动部署",
            "fix_hint": "在 .vivify.yml 中配置 deploy.method (ssh/command/webhook)",
        })

    # 7. Deploy URL
    deploy_url = project.get("deploy_url", "")
    if deploy_url:
        checks.append({
            "id": "deploy_url", "name": "部署地址", "category": "部署",
            "status": "ok", "message": deploy_url,
        })
    else:
        checks.append({
            "id": "deploy_url", "name": "部署地址", "category": "部署",
            "status": "warning", "message": "未配置部署地址，无法验证部署结果",
            "fix_hint": "设置 project.deploy_url 为站点访问地址",
        })

    # 8. GOALS.md
    goals_path = repo_root / "GOALS.md"
    if goals_path.exists() and goals_path.stat().st_size > 50:
        _goals_content = goals_path.read_text(encoding="utf-8", errors="replace")
        goal_count = _goals_content.count("## Goal:")
        checks.append({
            "id": "goals", "name": "项目目标", "category": "目标",
            "status": "ok", "message": f"已定义 {goal_count} 个目标",
        })
    else:
        checks.append({
            "id": "goals", "name": "项目目标", "category": "目标",
            "status": "missing", "message": "GOALS.md 未定义或为空",
            "fix_hint": "运行 vivify init 生成目标，或手动创建 GOALS.md",
        })

    # 9. Probes 配置
    probes_enabled = cfg.get("probes", {}).get("enabled", [])
    if probes_enabled:
        checks.append({
            "id": "probes", "name": "探针配置", "category": "监控",
            "status": "ok", "message": f"已启用 {len(probes_enabled)} 个探针",
        })
    else:
        checks.append({
            "id": "probes", "name": "探针配置", "category": "监控",
            "status": "warning", "message": "未启用任何探针",
            "fix_hint": "在 .vivify.yml 的 probes.enabled 中添加探针",
        })

    # 10. 状态数据库
    db_path = state_dir_path / "state.db" if state_dir_path else None
    if db_path and db_path.exists():
        checks.append({
            "id": "state_db", "name": "状态数据库", "category": "基础",
            "status": "ok", "message": "state.db 已初始化",
        })
    else:
        checks.append({
            "id": "state_db", "name": "状态数据库", "category": "基础",
            "status": "warning", "message": "state.db 不存在",
            "fix_hint": "运行 vivify run --once 初始化数据库",
        })

    ok_count = sum(1 for c in checks if c["status"] == "ok")
    total = len(checks)
    score = int(ok_count / total * 100) if total > 0 else 0

    return {
        "complete": ok_count == total,
        "score": score,
        "total_checks": total,
        "passed": ok_count,
        "checks": checks,
    }


def _get_instance_db(repo_path: Path) -> Optional[DashboardDB]:
    """为指定实例创建只读 DashboardDB 连接。"""
    state_dir = repo_path / ".vivify"
    db_path = state_dir / "state.db"
    if not db_path.exists():
        return None
    try:
        return DashboardDB(db_path)
    except Exception:
        return None


def _load_instances_registry() -> list:
    """加载全局实例注册表。"""
    if not INSTANCES_FILE.exists():
        return []
    try:
        return json.loads(INSTANCES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _validate_instance_path(repo_path: str) -> Path:
    """验证 instance 路径合法且包含 .vivify 目录。"""
    p = Path(repo_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"实例目录不存在: {repo_path}")
    if not (p / ".vivify").is_dir():
        raise HTTPException(status_code=404, detail=f"实例未初始化（缺少 .vivify 目录）: {repo_path}")
    return p


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

    # --- 多实例 API 端点 ---

    # 记录当前实例的 repo 路径（由 state_dir 反推）
    _current_repo = state_dir.parent.resolve() if state_dir else Path.cwd().resolve()

    @app.get("/api/instances")
    async def list_instances():
        """列出本机所有已知的 vivify 实例及其初始化信息。"""
        registry = _load_instances_registry()
        instances = []
        for entry in registry:
            repo = entry.get("repo", "")
            pid = entry.get("pid", 0)
            started_at = entry.get("started_at", "")
            repo_path = Path(repo)

            # 验证进程存活
            daemon_running = _is_process_alive(pid) if pid else False

            # 计算 uptime
            uptime_seconds = None
            if daemon_running and started_at:
                try:
                    start_dt = datetime.fromisoformat(started_at)
                    uptime_seconds = (datetime.now(timezone.utc) - start_dt).total_seconds()
                except (ValueError, TypeError):
                    uptime_seconds = None

            # 读取配置
            config = _read_vivify_config(repo_path)
            project = config.get("project", {})

            # 读取目标
            goals = _parse_goals(repo_path)

            # 获取最新 action 和 kpi
            last_action = None
            kpi_score = None
            instance_db = _get_instance_db(repo_path)
            if instance_db:
                try:
                    status_info = instance_db.get_status()
                    last_action = status_info.get("last_action")
                    # 获取最新 KPI 快照
                    kpi_rows = instance_db.get_kpi_snapshots(limit=1)
                    if kpi_rows:
                        kpi_score = kpi_rows[0].get("score")
                finally:
                    instance_db.close()

            instances.append({
                "id": _encode_instance_id(repo),
                "repo": repo,
                "project_name": project.get("name", repo_path.name),
                "scenario": project.get("type", "generic"),
                "language": project.get("language", ""),
                "framework": project.get("framework", ""),
                "deploy_url": project.get("deploy_url", ""),
                "goals": goals,
                "daemon_running": daemon_running,
                "daemon_pid": pid if daemon_running else None,
                "uptime_seconds": uptime_seconds,
                "last_action": last_action,
                "kpi_score": kpi_score,
                "state_dir": str(repo_path / ".vivify"),
            })

        return {
            "instances": instances,
            "current_instance": _encode_instance_id(str(_current_repo)),
        }

    @app.get("/api/instances/{instance_id}/config")
    async def get_instance_config(instance_id: str):
        """获取指定实例的完整初始化配置。"""
        try:
            repo_str = _decode_instance_id(instance_id)
        except Exception:
            raise HTTPException(status_code=400, detail="无效的 instance_id")

        repo_path = _validate_instance_path(repo_str)
        config = _read_vivify_config(repo_path)
        goals_md, goals_list = _parse_goals_detailed(repo_path)

        # 获取初始化时间
        vivify_dir = repo_path / ".vivify"
        initialized_at = None
        try:
            stat = vivify_dir.stat()
            initialized_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
        except OSError:
            pass

        return {
            "repo": repo_str,
            "config": config,
            "goals_md": goals_md,
            "goals_list": goals_list,
            "state_dir": str(vivify_dir),
            "initialized_at": initialized_at,
        }

    @app.get("/api/instances/{instance_id}/status")
    async def get_instance_status(instance_id: str):
        """获取指定实例的运行状态。"""
        try:
            repo_str = _decode_instance_id(instance_id)
        except Exception:
            raise HTTPException(status_code=400, detail="无效的 instance_id")

        repo_path = _validate_instance_path(repo_str)
        instance_state_dir = repo_path / ".vivify"

        # 检测 daemon 状态
        pid_file = instance_state_dir / "vivify.pid"
        daemon_running = False
        daemon_pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                if _is_process_alive(pid):
                    daemon_running = True
                    daemon_pid = pid
            except (ValueError, OSError):
                pass

        instance_db = _get_instance_db(repo_path)
        if not instance_db:
            return {
                "daemon_running": daemon_running,
                "daemon_pid": daemon_pid,
                "last_action": None,
                "latest_round": None,
            }

        try:
            status_info = instance_db.get_status()
            rounds = instance_db.get_rounds(limit=1)
            return {
                "daemon_running": daemon_running,
                "daemon_pid": daemon_pid,
                "last_action": status_info.get("last_action"),
                "latest_round": rounds[0] if rounds else None,
            }
        finally:
            instance_db.close()

    @app.get("/api/instances/{instance_id}/actions")
    async def get_instance_actions(
        instance_id: str,
        limit: int = Query(50, le=200),
        category: Optional[str] = None,
        action_type: Optional[str] = None,
        since: Optional[str] = None,
    ):
        """获取指定实例的操作日志。"""
        try:
            repo_str = _decode_instance_id(instance_id)
        except Exception:
            raise HTTPException(status_code=400, detail="无效的 instance_id")

        repo_path = _validate_instance_path(repo_str)
        instance_db = _get_instance_db(repo_path)
        if not instance_db:
            return []

        try:
            return instance_db.get_recent_actions(
                limit=limit, category=category, action_type=action_type, since=since
            )
        finally:
            instance_db.close()

    @app.get("/api/instances/{instance_id}/kpi/snapshots")
    async def get_instance_kpi(
        instance_id: str,
        since: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = Query(100, le=500),
    ):
        """获取指定实例的 KPI 快照。"""
        try:
            repo_str = _decode_instance_id(instance_id)
        except Exception:
            raise HTTPException(status_code=400, detail="无效的 instance_id")

        repo_path = _validate_instance_path(repo_str)
        instance_db = _get_instance_db(repo_path)
        if not instance_db:
            return []

        try:
            return instance_db.get_kpi_snapshots(since=since, source=source, limit=limit)
        finally:
            instance_db.close()

    @app.get("/api/config/health")
    async def config_health():
        """检查当前实例的配置完整性。"""
        return _check_config_health(_current_repo, state_dir)

    @app.get("/api/instances/{instance_id}/config/health")
    async def instance_config_health(instance_id: str):
        """检查指定实例的配置完整性。"""
        try:
            repo_str = _decode_instance_id(instance_id)
        except Exception:
            raise HTTPException(status_code=400, detail="无效的 instance_id")
        repo_path = _validate_instance_path(repo_str)
        return _check_config_health(repo_path, repo_path / ".vivify")

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
