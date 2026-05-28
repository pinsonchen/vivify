"""Generate and parse project repowiki via ``qodercli wiki``.

The wiki provides AI-generated architecture / module / source-file insight
for any repo. ``vivify init`` invokes this once so downstream stages
(AI analyzer, feature pipeline) can ground their reasoning on a richer,
project-specific context.

Failure is non-fatal: callers should treat a ``None``/empty result as
"wiki unavailable, proceed with whatever signals exist".
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 相对于 repo 根的固定输出目录 (qodercli wiki --language zh 的产物)
DEFAULT_WIKI_DIR = ".qoder/repowiki/zh"
METADATA_REL_PATH = "meta/repowiki-metadata.json"

# 元数据文件可能很大；超过该阈值时以更保守的方式读取
_LARGE_METADATA_THRESHOLD = 20 * 1024 * 1024  # 20 MB


@dataclass
class WikiContext:
    """Light-weight, JSON-friendly summary of a generated repowiki.

    Only contains a small slice of the metadata file (overview text, top
    source files, catalog titles, snippet count) so it can be safely
    serialised, embedded into AI prompts, or stored in memory.
    """

    wiki_path: str = ""                       # 相对路径，例如 ".qoder/repowiki/zh"
    overview: str = ""                        # 项目架构概览（截断到 ~2000 字符）
    source_files: List[str] = field(default_factory=list)
    catalogs: List[str] = field(default_factory=list)
    snippet_count: int = 0
    source_file_count: int = 0
    catalog_count: int = 0

    def is_empty(self) -> bool:
        return not (self.overview or self.source_files or self.catalogs)

    def to_prompt_block(
        self,
        *,
        max_overview_chars: int = 1200,
        max_source_files: int = 25,
        max_catalogs: int = 15,
    ) -> str:
        """Render this context as a Markdown block to append to AI prompts."""
        if self.is_empty():
            return ""

        parts: List[str] = ["## 项目 Wiki 分析结果"]
        parts.append(f"- 代码片段索引: {self.snippet_count} 个")
        parts.append(f"- 关键源文件总数: {self.source_file_count} 个")
        parts.append(f"- Wiki 文档章节数: {self.catalog_count} 个")

        if self.source_files:
            top_files = self.source_files[:max_source_files]
            parts.append("- 核心源文件:")
            for f in top_files:
                parts.append(f"  - {f}")
            if len(self.source_files) > max_source_files:
                parts.append(f"  - ... 以及其他 {len(self.source_files) - max_source_files} 个文件")

        if self.catalogs:
            top_cats = self.catalogs[:max_catalogs]
            parts.append("- 文档结构:")
            for c in top_cats:
                parts.append(f"  - {c}")
            if len(self.catalogs) > max_catalogs:
                parts.append(f"  - ... 以及其他 {len(self.catalogs) - max_catalogs} 个章节")

        if self.overview:
            ov = self.overview.strip()
            if len(ov) > max_overview_chars:
                ov = ov[:max_overview_chars].rstrip() + " …"
            parts.append("- 架构概览:")
            for line in ov.splitlines():
                parts.append(f"  > {line}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_wiki(
    repo_root: Path,
    *,
    qodercli_path: str = "qodercli",
    language: str = "zh",
    timeout_seconds: int = 120,
    permission_mode: str = "bypass_permissions",
) -> tuple[bool, str]:
    """Invoke ``qodercli wiki`` for *repo_root*.

    Returns ``(success, info)``. ``info`` carries either the version/status
    string on success or the error reason on failure. Never raises.
    """
    binary = shutil.which(qodercli_path) or qodercli_path
    if not binary or (not os.path.isabs(binary) and not shutil.which(binary)):
        return False, f"qodercli binary not found ({qodercli_path!r})"

    cmd = [
        binary,
        "wiki",
        "--repo", str(repo_root),
        "--language", language,
        "--permission-mode", permission_mode,
    ]

    env = dict(os.environ)
    env.setdefault("TERM", "dumb")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd=str(repo_root),
        )
    except subprocess.TimeoutExpired:
        return False, f"qodercli wiki timed out after {timeout_seconds}s"
    except (FileNotFoundError, OSError) as exc:
        return False, f"qodercli wiki launch failed: {exc}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return False, f"qodercli wiki exit={result.returncode}: {err[:300]}"

    return True, "ok"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_wiki_metadata(
    repo_root: Path,
    *,
    wiki_dir: str = DEFAULT_WIKI_DIR,
) -> Optional[WikiContext]:
    """Parse ``meta/repowiki-metadata.json`` produced by ``qodercli wiki``.

    Returns ``None`` if the metadata file does not exist or cannot be
    parsed. Only a small slice of the (potentially huge) JSON is retained
    in the resulting :class:`WikiContext`.
    """
    meta_path = repo_root / wiki_dir / METADATA_REL_PATH
    if not meta_path.is_file():
        return None

    size = 0
    try:
        size = meta_path.stat().st_size
    except OSError:
        pass

    try:
        # 即便文件较大 (~100MB)，json.load 仍是最可靠的解析方式；
        # 解析后我们立即丢弃原始 dict 仅保留小切片。
        with meta_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("parse_wiki_metadata: failed to load %s: %s", meta_path, exc)
        return None

    if not isinstance(data, dict):
        return None

    if size >= _LARGE_METADATA_THRESHOLD:
        logger.info(
            "parse_wiki_metadata: metadata file is large (%.1f MB); only a slice will be kept in memory",
            size / (1024 * 1024),
        )

    overview = _extract_overview(data.get("wiki_overview"))
    source_files = _extract_source_files(data.get("source_files"))
    catalogs = _extract_catalogs(data.get("wiki_catalogs"))
    snippet_count = _safe_len(data.get("code_snippets"))

    ctx = WikiContext(
        wiki_path=wiki_dir,
        overview=overview,
        source_files=source_files,
        catalogs=catalogs,
        snippet_count=snippet_count,
        source_file_count=_safe_len(data.get("source_files")),
        catalog_count=_safe_len(data.get("wiki_catalogs")),
    )

    # 释放大对象引用（让 GC 尽快回收）
    del data
    return ctx


def load_wiki_context_if_available(
    repo_root: Path,
    *,
    wiki_dir: str = DEFAULT_WIKI_DIR,
) -> Optional[WikiContext]:
    """Convenience wrapper: silently return ``None`` if no wiki present."""
    try:
        return parse_wiki_metadata(repo_root, wiki_dir=wiki_dir)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("load_wiki_context_if_available: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_len(value) -> int:
    try:
        return len(value)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        return 0


def _extract_overview(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        # qodercli wiki 当前版本将概览放在 ``content`` 字段
        for key in ("content", "text", "summary", "overview"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _extract_source_files(raw, *, limit: int = 100) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if len(out) >= limit:
            break
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            path = item.get("path") or item.get("filename") or item.get("file")
            if isinstance(path, str) and path:
                out.append(path)
    return out


def _extract_catalogs(raw, *, limit: int = 50) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if len(out) >= limit:
            break
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title")
            if isinstance(name, str) and name:
                out.append(name)
    return out


__all__ = [
    "WikiContext",
    "DEFAULT_WIKI_DIR",
    "METADATA_REL_PATH",
    "generate_wiki",
    "parse_wiki_metadata",
    "load_wiki_context_if_available",
]
