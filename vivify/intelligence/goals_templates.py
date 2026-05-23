"""场景化 GOALS.md 模板。"""

from __future__ import annotations

GOALS_HEADER = """---
version: 1
owner: "@{owner}"
review_cadence: weekly
---

# Project Goals

This file is read by `vivify goals decompose` to derive concrete
FeatureRequests on a schedule. Each goal must declare at least one KPI.
"""


SCENARIO_GOALS: dict[str, str] = {
    "docs-only": """
## Goal: 保持文档内容时效性
确保所有文档内容与实际情况同步，过时内容及时更新。

- KPI: doc_staleness target=<=30days direction=down unit=days
- KPI: dead_links target=0 direction=down unit=count
- Deadline: 2026-Q4
- Notes: 定期检查外部链接有效性，平台政策变更后及时更新文档。

## Goal: 维护站点可用性
确保在线站点持续可达，页面正常渲染。

- KPI: site_uptime target=>=99% direction=up unit=%
- Deadline: 2026-Q3
- Notes: 监控部署地址响应状态。
""",
    "static-site": """
## Goal: 保持站点内容时效性
确保站点内容准确、链接有效。

- KPI: doc_staleness target=<=30days direction=down unit=days
- KPI: dead_links target=0 direction=down unit=count
- Deadline: 2026-Q4

## Goal: 维护站点可用性
确保部署站点持续可达，加载速度正常。

- KPI: site_uptime target=>=99% direction=up unit=%
- KPI: page_load_time target=<=3s direction=down unit=seconds
- Deadline: 2026-Q3
""",
    "web-app": """
## Goal: 保持 CI 稳定性
让每次 CI 失败都反映真实的代码问题。

- KPI: ci_pass_rate target=>=95% direction=up unit=%
- Deadline: 2026-Q3
- Notes: 关注 flaky tests，减少误报。

## Goal: 提升测试覆盖率
覆盖关键路径，稳步提升覆盖率下限。

- KPI: line_coverage target=>=80% direction=up unit=%
- KPI: branch_coverage target=>=70% direction=up unit=%
- Deadline: 2026-Q4

## Goal: 控制依赖安全性
保持零已知高危漏洞。

- KPI: critical_vulnerabilities target=0 direction=down unit=count
- Deadline: 2026-Q3
""",
    "api-service": """
## Goal: 保持 API 服务可靠性
确保服务稳定运行，减少故障时间。

- KPI: ci_pass_rate target=>=98% direction=up unit=%
- KPI: error_rate target=<=1% direction=down unit=%
- Deadline: 2026-Q3

## Goal: 提升测试覆盖率
重点覆盖核心业务逻辑和边界场景。

- KPI: line_coverage target=>=85% direction=up unit=%
- Deadline: 2026-Q4

## Goal: 控制依赖安全性
及时更新有安全漏洞的依赖。

- KPI: critical_vulnerabilities target=0 direction=down unit=count
- Deadline: 2026-Q3
""",
    "python-package": """
## Goal: 保持 CI 稳定性
确保所有测试通过，构建可靠。

- KPI: ci_pass_rate target=>=98% direction=up unit=%
- Deadline: 2026-Q3

## Goal: 提升测试覆盖率
优先覆盖公共 API 和关键路径。

- KPI: line_coverage target=>=90% direction=up unit=%
- KPI: branch_coverage target=>=80% direction=up unit=%
- Deadline: 2026-Q4

## Goal: 减少代码质量问题
控制 lint 错误和死代码。

- KPI: lint_errors target=0 direction=down unit=count
- Deadline: 2026-Q3
""",
    "cli-tool": """
## Goal: 保持 CLI 可靠性
确保所有命令正常工作，无回归。

- KPI: ci_pass_rate target=>=98% direction=up unit=%
- KPI: line_coverage target=>=80% direction=up unit=%
- Deadline: 2026-Q4

## Goal: 控制代码质量
消除 lint 错误和冗余代码。

- KPI: lint_errors target=0 direction=down unit=count
- Deadline: 2026-Q3
""",
    "mobile-app": """
## Goal: 保持构建稳定性
确保移动端构建持续通过。

- KPI: ci_pass_rate target=>=95% direction=up unit=%
- Deadline: 2026-Q3

## Goal: 控制依赖安全性
及时更新依赖中的已知漏洞。

- KPI: critical_vulnerabilities target=0 direction=down unit=count
- Deadline: 2026-Q4
""",
    "monorepo": """
## Goal: 保持所有子项目 CI 稳定
确保每个模块的构建和测试通过。

- KPI: ci_pass_rate target=>=95% direction=up unit=%
- KPI: build_duration target=<=10min direction=down unit=min
- Deadline: 2026-Q3

## Goal: 提升整体测试覆盖率
各模块覆盖率均达到基线。

- KPI: line_coverage target=>=75% direction=up unit=%
- Deadline: 2026-Q4
""",
    "infra": """
## Goal: 保持基础设施代码安全
检测配置中的安全隐患和密钥泄露。

- KPI: secrets_detected target=0 direction=down unit=count
- Deadline: 2026-Q3

## Goal: 保持仓库整洁
清理陈旧分支和过时配置。

- KPI: stale_branches target=<=5 direction=down unit=count
- Deadline: 2026-Q4
""",
    "generic": """
## Goal: 保持代码健康
减少 lint 错误和依赖漏洞。

- KPI: lint_errors target=0 direction=down unit=count
- KPI: critical_vulnerabilities target=0 direction=down unit=count
- Deadline: 2026-Q4

## Goal: 保持仓库整洁
控制陈旧分支和仓库大小。

- KPI: stale_branches target=<=5 direction=down unit=count
- Deadline: 2026-Q3
""",
}


def render_goals(scenario: str, owner: str = "team") -> str:
    """根据场景生成完整 GOALS.md 内容。

    未知场景回退到 ``generic``。
    """
    body = SCENARIO_GOALS.get(scenario) or SCENARIO_GOALS["generic"]
    header = GOALS_HEADER.format(owner=owner.lstrip("@") or "team")
    return header + body
