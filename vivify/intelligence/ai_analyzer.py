"""AI-powered project analyzer using qodercli."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .scanner import ProjectSignals
from .classifier import ScenarioType
from .wiki_generator import WikiContext


# 匹配 fenced JSON 块或裸 JSON 对象
_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL
)
_BARE_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


@dataclass
class AIAnalysisResult:
    """qodercli AI 分析返回的结构化结果。"""
    scenario: str
    language: str
    framework: str
    description: str
    deploy_url: str
    test_command: str
    build_command: str
    dev_command: str
    health_endpoint: str
    goals_markdown: str
    reasoning: str
    confidence: float


class AIAnalyzer:
    """通过 qodercli 进行 AI 驱动的项目智能分析。"""

    def __init__(self, binary_path: str = "qodercli", model: str = "ultimate"):
        self.binary_path = binary_path
        self.model = model

    def is_available(self) -> tuple[bool, str]:
        """检测 qodercli 是否可用。返回 (可用, 版本/错误信息)。"""
        binary = shutil.which(self.binary_path)
        if not binary:
            return False, f"{self.binary_path} not found in PATH"
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"
                return True, version
            return False, f"exit code {result.returncode}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return False, str(e)

    def analyze(
        self,
        repo_root: Path,
        signals: ProjectSignals,
        wiki_context: Optional[WikiContext] = None,
    ) -> Optional[AIAnalysisResult]:
        """
        调用 qodercli 分析项目，返回结构化结果。
        失败时返回 None（调用方应 fallback 到规则引擎）。
        可选传入 ``wiki_context`` 将项目 Wiki 架构信息附加到提示词中。
        """
        prompt = self._build_prompt(signals, wiki_context=wiki_context)
        binary = shutil.which(self.binary_path) or self.binary_path

        cmd = [
            binary,
            "-p", prompt,
            "--yolo", "-q",
            "--model", self.model,
            "--max-turns", "1",
            "-w", str(repo_root),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._build_env(),
            )
            if result.returncode == 0 and result.stdout:
                return self._parse_output(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def _build_prompt(
        self,
        signals: ProjectSignals,
        wiki_context: Optional[WikiContext] = None,
    ) -> str:
        """构造分析提示词，要求 AI 以 JSON 格式返回项目分析结果。"""
        # 项目文件列表（最多 50 个）
        files_sample = signals.files[:50]
        files_str = "\n".join(f"  - {f}" for f in files_sample)
        if len(signals.files) > 50:
            files_str += f"\n  ... 和其他 {len(signals.files) - 50} 个文件"

        # README 内容（前 2000 字符）
        readme_excerpt = ""
        if signals.readme_content:
            readme_excerpt = signals.readme_content[:2000]

        # 包管理信息
        pkg_info_parts = []
        if signals.has_package_json:
            pkg_info_parts.append("package.json 存在")
        if signals.has_pyproject_toml:
            pkg_info_parts.append("pyproject.toml 存在")
        if signals.has_requirements_txt:
            pkg_info_parts.append("requirements.txt 存在")
        if signals.has_cargo_toml:
            pkg_info_parts.append("Cargo.toml 存在")
        if signals.has_go_mod:
            pkg_info_parts.append("go.mod 存在")
        pkg_info = ", ".join(pkg_info_parts) if pkg_info_parts else "无包管理文件"

        # 框架
        frameworks = ", ".join(signals.detected_frameworks) if signals.detected_frameworks else "未检测到"

        # URL
        urls = "\n".join(f"  - {u}" for u in signals.readme_urls[:10]) if signals.readme_urls else "  无"

        # 扩展名统计
        top_exts = signals.file_extensions.most_common(10)
        exts_str = ", ".join(f"{ext}({count})" for ext, count in top_exts) if top_exts else "无"

        # 测试信息
        test_info = ""
        if signals.test_command:
            test_info = f"测试命令: {signals.test_command}"
        elif signals.test_framework:
            test_info = f"测试框架: {signals.test_framework}"

        # scripts
        scripts_str = ""
        if signals.scripts:
            scripts_str = ", ".join(f"{k}: {v}" for k, v in list(signals.scripts.items())[:10])

        # 有效场景类型
        valid_scenarios = [s.value for s in ScenarioType]

        # Wiki 上下文（可选）
        wiki_block = ""
        if wiki_context is not None and not wiki_context.is_empty():
            wiki_block = "\n\n" + wiki_context.to_prompt_block() + "\n"

        prompt = f"""你是一个项目分析专家。请分析以下项目信息，并以 JSON 格式返回分析结果。

## 项目信息

**项目名称**: {signals.project_name or "未知"}
**项目描述**: {signals.project_description or "未知"}
**文件数量**: {signals.total_files}
**文件扩展名分布**: {exts_str}
**包管理**: {pkg_info}
**检测到的框架**: {frameworks}
**CI/CD**: {signals.ci_provider or "未检测到"}
**测试**: {test_info or "未检测到"}
**Scripts**: {scripts_str or "无"}
**Git 远程**: {signals.git_remote_url or "未知"}

**文件列表**:
{files_str}

**README 中的 URL**:
{urls}

**README 内容摘要**:
{readme_excerpt}
{wiki_block}
## 要求

请分析此项目并返回以下 JSON 格式（必须用 ```json ``` 代码块包裹）：

```json
{{
  "scenario": "<项目场景类型，必须为以下之一: {', '.join(valid_scenarios)}>",
  "language": "<主要编程语言>",
  "framework": "<主要框架，无则空字符串>",
  "description": "<一句话项目描述>",
  "deploy_url": "<部署地址URL，从README或项目信息中发现，无则空字符串>",
  "test_command": "<测试命令，无则空字符串>",
  "build_command": "<构建命令，无则空字符串>",
  "dev_command": "<开发服务启动命令，无则空字符串>",
  "health_endpoint": "<健康检查端点，仅API服务需要，无则空字符串>",
  "goals_markdown": "<为此项目量身定制的 GOALS.md 内容，包含 2-3 个切合项目实际的目标和 KPI，使用标准 vivify GOALS 格式>",
  "reasoning": "<分析理由，简要说明为何选择此场景类型>",
  "confidence": <置信度 0.0-1.0>
}}
```

GOALS.md 格式要求：
- 每个 Goal 格式：## Goal: <标题>
- 每个 KPI 格式：- KPI: <name> target=<expr> direction=<up|down|stable> unit=<unit>
- 包含 Deadline 和 Notes
- 目标必须与项目的实际业务场景相关（不要用通用模板）

请只输出 JSON，不要有其他解释文字。"""

        return prompt

    def _parse_output(self, output: str) -> Optional[AIAnalysisResult]:
        """从 qodercli 输出中提取 JSON 结果。"""
        # 过滤 hook timing 行
        lines = [
            line for line in output.splitlines()
            if not line.startswith("[hook timing]")
        ]
        cleaned = "\n".join(lines)

        # 尝试提取 fenced JSON
        match = _JSON_BLOCK_RE.search(cleaned)
        json_str = match.group(1) if match else None

        # fallback: 尝试裸 JSON
        if not json_str:
            match = _BARE_JSON_RE.search(cleaned)
            json_str = match.group(0) if match else None

        if not json_str:
            return None

        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return None

        # 验证必要字段
        if "scenario" not in data:
            return None

        # 验证 scenario 是有效值
        valid_scenarios = {s.value for s in ScenarioType}
        scenario = data.get("scenario", "generic")
        if scenario not in valid_scenarios:
            scenario = "generic"

        return AIAnalysisResult(
            scenario=scenario,
            language=data.get("language", ""),
            framework=data.get("framework", ""),
            description=data.get("description", ""),
            deploy_url=data.get("deploy_url", ""),
            test_command=data.get("test_command", ""),
            build_command=data.get("build_command", ""),
            dev_command=data.get("dev_command", ""),
            health_endpoint=data.get("health_endpoint", ""),
            goals_markdown=data.get("goals_markdown", ""),
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.8)),
        )

    def _build_env(self) -> dict[str, str]:
        """构建执行环境，继承当前环境并设置必要变量。"""
        env = dict(os.environ)
        env["TERM"] = "dumb"
        return env
