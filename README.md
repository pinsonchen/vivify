# 复元·Vivify

> 万物皆可自愈，系统由此新生。

**全场景系统自生长赋能引擎** — The Universal Engine for System Evolution

---

复元·Vivify 是一个自学习的智能扩展系统，可以挂载到任何 GitHub 项目。它作为系统的"第二大脑"与"生命源泉"，通过自主监控、自动修复和迭代开发来维持项目健康，让每一个系统都拥有进化的灵魂。

## 核心理念

在万物互联的数字宇宙中，每个长期项目都会逐渐"腐化"——测试腐化、依赖老化、代码债增长、目标遗失。传统的维护方式是"打补丁"，而我们选择"植入生命"。

**外挂赋能三部曲：**

1. **无感接入，即插即用** — 无论是陈旧的工业代码、脆弱的服务器集群，只需接入 Vivify，即可瞬间获得"生命体征"。
2. **共生修复，固本培元** — 当系统遭遇故障，Vivify 作为强大的外部免疫系统介入，不仅快速修复损伤，更会反向优化底层逻辑。
3. **自主生长，无限进化** — 接入后，系统能根据环境变化自主衍生新功能、新策略，实现从"被使用"到"自我进化"的跨越。

## 工作原理

```
检测(Probes) → 直接修复(Fixers) → AI修复(CodingAgent) → 验证(Verifier) → 上报(Escalator)
```

1. **检测** — 12+ 可插拔探针持续监控（CI 失败、漏洞、覆盖率、lint 债务、Issue 积压、文档过期、死代码、秘密泄露…）
2. **修复** — 内置修复器安全处理可自动修复的问题（依赖更新、lint/format 自动修复、不稳定测试分流、陈旧分支清理…）
3. **升级** — 无法自动修复的问题升级为需求（存储于 SQLite + 镜像到 GitHub Issues）
4. **迭代** — 读取 `GOALS.md`，将每个目标分解为可执行的特性请求，通过 Qoder CLI 在隔离的 `git worktree` 中开发
5. **落地** — 每个变更通过 Pull Request 提交，不直接推送 `main`
6. **自生长** — AI 可优化自身的探针/修复器/提示词（在路径白名单约束下），系统越用越锋利

## 快速开始

```bash
pip install vivify-cli            # 或: pip install -e . (从源码)
cd /path/to/your/repo
vivify init                       # 交互式初始化
vivify doctor                     # 验证环境 (git, gh, qodercli, GH_TOKEN)
vivify run --once --dry-run       # 预览检测结果（不创建 PR）
vivify run                        # 启动守护进程
```

## 一键安装

**macOS / Linux / WSL：**
```bash
curl -fsSL https://raw.githubusercontent.com/pinsonchen/vivify/main/install.sh | sh
```

**Windows PowerShell：**
```powershell
irm https://raw.githubusercontent.com/pinsonchen/vivify/main/install.ps1 | iex
```

安装脚本会自动检测 Python 环境、安装 vivify-cli，并验证外部依赖。

## 环境要求

- Python ≥ 3.10
- `git`、`gh` (GitHub CLI)、`qodercli` 在 `PATH` 中可用
- `GH_TOKEN` 环境变量（或已执行 `gh auth login`）
- GitHub 仓库已启用分支保护（推荐，用于 `auto_merge`）

## 接入后的项目结构

```
.vivify.yml                   # 配置文件（提交；敏感信息走环境变量）
GOALS.md                      # 项目目标与 KPI（提交）
.vivify/
├── state.db                  # SQLite — 特性池、日志、知识库（gitignored）
├── logs/                     # 每日日志（gitignored）
├── worktrees/                # AI 开发分支（gitignored）
├── probes/                   # 自定义探针（.py / .yml）
├── fixers/                   # 自定义修复器（.py）
└── pr_template.md            # PR 正文模板
```

## 核心概念

- **Probe** — 声明式(YAML)或编程式(Python)检测器，输出 `Issue`
- **Fixer** — 无需 LLM 的快速修复路径（如 `ruff --fix`），直接发起 PR
- **Issue** → 多轮未修复 → **FeatureRequest** → 评估 → worktree 开发 → PR → 验证 → 知识沉淀
- **Goal**（`GOALS.md`）→ KPI → KPI 监控探针 → 降级告警 → 目标分解器 → 新特性请求
- **Self-growth**：AI 可编辑 `vivify/probes/builtin/`、`vivify/fixers/builtin/`、提示模板，但内核修改需两人审批

## CLI 命令

```
vivify init [--non-interactive] [--repo PATH] [--force]
vivify run  [--once] [--dry-run] [--category CAT] [--interval N]
vivify start [--extra-args ...]
vivify stop  [--force]
vivify status
vivify list
vivify doctor
vivify goals    show | add ... | decompose [--goal NAME] [--dry-run]
vivify probes   list | test <id> | enable/disable <id>
vivify fixers   list | test <id> --issue-file FILE
vivify features list [--status S] | show <id> | retry <id>
vivify logs     tail [-n N] [--follow]
```

## 多实例 Daemon 管理

Vivify 支持在不同项目目录下独立运行 daemon 实例，每个实例通过 PID 文件和文件锁实现隔离。

```bash
# 后台启动（当前目录绑定）
vivify start

# 查看状态
vivify status

# 列出本机所有运行中的实例
vivify list

# 停止
vivify stop

# 强制停止
vivify stop --force
```

每个项目目录下只允许运行一个 vivify 实例。状态数据存储在 `.vivify/` 目录中，各实例完全隔离。

## 配置

参见 `.vivify.example.yml`。所有配置项可通过环境变量覆盖：`VIVIFY__<DOTTED_PATH>`（路径段用双下划线分隔）。

## 状态

Alpha (v0.1.0)。内核 + SQLite 存储 + Qoder CLI 代理 + PR 模式 + 12 个内置探针 + 7 个内置修复器 + 目标分解器已就绪。

**路线图**：GitHub Actions Runner、Docker 镜像、多代理支持 (Claude Code/Codex)、Web 控制台。

## 许可证

MIT — 见 `LICENSE`。

---

*复元·Vivify — 让每一个系统，都拥有进化的灵魂。*
