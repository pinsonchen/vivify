"""SSE 实时日志流。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


async def tail_log(log_path: Path):
    """异步生成器：监控日志文件并 yield SSE 事件。"""
    if not log_path.exists():
        yield f"data: {json.dumps({'line': '[等待日志文件创建...]'})}\n\n"
        while not log_path.exists():
            await asyncio.sleep(1)

    with open(log_path, encoding="utf-8", errors="replace") as f:
        # 跳到文件末尾
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
            else:
                await asyncio.sleep(0.5)
