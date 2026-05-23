"""vivify dashboard – launch the web UI."""
from __future__ import annotations

import argparse
import sys
import webbrowser


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("dashboard", help="启动 Web Dashboard")
    p.add_argument("--port", type=int, default=9120, help="监听端口 (默认 9120)")
    p.add_argument("--host", default="127.0.0.1", help="监听地址")
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "错误: 缺少 dashboard 依赖，请安装: pip install vivify[dashboard]",
            file=sys.stderr,
        )
        sys.exit(1)

    from vivify.config.loader import find_state_dir
    from vivify.dashboard.app import create_app

    state_dir = find_state_dir()
    if state_dir is None:
        print("错误: 未找到 .vivify 目录，请先执行 vivify init", file=sys.stderr)
        sys.exit(1)

    app = create_app(state_dir)

    if not args.no_open:
        import threading

        url = f"http://{args.host}:{args.port}"
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
