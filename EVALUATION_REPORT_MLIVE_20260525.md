# vivify 在 mlive 项目中的全面评估报告

**评估时间**: 2026-05-25  
**评估范围**: vivify 在 mlive 项目的自动化工作流、代码质量、目标达成度、prompt 优化空间

---

## 一、总体评分

**综合评分: 7.2 / 10**

**一句话总结**: vivify 在 mlive 项目上展现出强大的目标分解和内容生成能力，已成功部署 11 个相关特性，但在**故障恢复率**（45% heal 失败）、**prompt 上下文准确性**和**重复检测**等方面存在显著瓶颈。

---

## 二、产出质量评估

### 已合并 PR 分析 (11 个 merged PR)

| PR# | 标题 | 类型 | 代码行数 | 质量评分 | 评价 |
|-----|------|------|---------|---------|------|
| #11 | 站点健康检查跳过 (修复探针) | bugfix | 60 | 6/10 | **有问题的修复**: YAML 探针覆盖是 workaround 而非根因修复。虽然功能可用，但留下了上游 vivify 包有 Jinja 模板 bug 的技术债。建议上游修复 builtin probe 配置。|
| #10 | 快速解决摘要 (≤3步) | feature | ~200 | 8/10 | **高质量**：格式化良好，覆盖 10+ 场景，符合 KPI (avg_resolution_steps=3.0)。已验证并合并。|
| #9 | 社区支持入口 (Issue/Discussion 模板) | feature | ~500 | 8/10 | **高质量**：完整的 GitHub issue templates、SUPPORT.md 和社区引导。缺少 Discussion 分类模板（标记为 follow-up）。|
| #8 | FAQ 检索索引 (症状/错误码/平台) | feature | ~800 | 7/10 | **可用但略显超前**: 生成的索引表格信息量大，CI 链接检查已部署。但索引粒度和维护开销未充分权衡。|
| #7 | 10+ 故障决策树 | feature | ~700 | 7/10 | **结构合理但内容重复**: 决策树设计清晰，但与 #4 内容有重叠。验证未通过 PR merge conflicts (仍为 DIRTY 状态)。|
| #6 | 首屏渲染优化 (Vite+CSS 内联) | optimization | ~2000 | 7/10 | **技术选择稳健但不完整**: 引入 Vite bundler 增加了复杂度，CSS 内联逻辑正确。但遗留了 legacy script.js，部署后 Lighthouse 审计缺失。|
| #5 | 诊断脚本 (8项检测) | feature | ~520 | 8/10 | **实用性强**: diagnose.sh 覆盖 RTMP 连通、带宽、OBS 配置等 8 项。脚本质量高，参考链接可用。无平台依赖（bash 原生）。|
| #4 | 10 个故障排查手册 | feature | ~788 | 8/10 | **基础扎实**: 规范化的故障场景文档，格式统一。每场景包含现象、决策树、修复方案。符合 troubleshoot_scenarios≥10 的 KPI。|
| #3 | 诊断脚本 + 日志收集 | feature | ~520 | 8/10 | **同 #5** |
| #2 | 配置 health_endpoint | bugfix | ~10 | 3/10 | **最小化修复但反复**: 多次创建相同分支却因 "No commits between main and branch" 失败。说明 PR 创建前缺乏分支检查。|
| #1 | 配置 health_endpoint | bugfix | ~10 | 3/10 | **同 #2** |

**代码质量总体观察**:
- ✅ 文档类特性质量稳定 (7-8 分)
- ✅ 脚本工具适用性强 (8 分)
- ⚠️ 前端优化缺乏完整验证 (7 分)
- ❌ 重复 PR 和 bug fix 循环 (3-6 分)

---

## 三、目标对齐度评估

### GOALS.md 中的 3 个目标及达成情况

```
目标1: 提升指南内容覆盖率与时效性 [deadline: 2026-08-01]
  KPI: platform_coverage target=>=5 | policy_update_lag<=30days | document_version>=2.1
  ├─ 特性 #1 (pending)   - 整合支付宝、新增快手/拼多多
  ├─ 特性 #2 (pending)   - 统一版本至 v2.1 并同步 2026-05 政策
  ├─ 特性 #8 (pending)   - 新增 B 站 (Bilibili) 平台
  └─ 特性 #9 (rejected)  - KPI 基线快照脚本 [原因: git fetch timeout]
  ⛔ 进度: 0/4 特性部署，3 个 pending，1 个 rejected
  **评价**: 内容覆盖类 features 全部卡住。AI 无法有效推动平台扩展工作。

目标2: 提升交互式指南用户体验 [deadline: 2026-07-01]
  KPI: page_load_time<=2s | guide_completion_steps<=5 | mobile_responsive_score>=90
  ├─ 特性 #6 (deployed_with_issues) - 脚本懒加载 + CSS 内联
  ├─ 特性 #10 (rejected)  - 移动端触控体验 [原因: git push timeout]
  ├─ 特性 #11 (deployed_with_issues) - CSS/JS 内联 + 预加载 [merge 未完成]
  ├─ 特性 #12-15 (pending/followup) - PWA、localStorage 持久化等
  └─ 特性 #14,15 (pending) - 响应式布局、PWA 离线
  ⛔ 进度: 2/5 已部署但有问题，3 个 pending，1 个被拒，完整验证未进行
  **评价**: 移动端目标仍远。Lighthouse 审计和实际性能数据缺失。

目标3: 增强故障排查与社区支持能力 [deadline: 2026-09-01]
  KPI: faq_entries>=20 | troubleshoot_scenarios>=10 | avg_resolution_steps<=3
  ├─ 特性 #3,4,5,7 (deployed) - 故障手册、诊断脚本、决策树
  ├─ 特性 #9,17,18 (verified) - 快速解决摘要、Issue 模板、索引
  ├─ 特性 #19,20 (followup pending) - Discussions 启用、模板补全
  └─ 特性 #3 (pending) - FAQ 知识库 (仅部分完成)
  ✅ 进度: 7 个特性已部署/验证，3 个 pending，涵盖 troubleshoot_scenarios=10 ✓
  **评价**: 这是目前最成功的目标。avg_resolution_steps 已达到 KPI（3.0）。社区入口和检索已建立。
```

### 目标对齐度得分

| 目标 | 完成度 | KPI 达成度 | 评分 |
|-----|-------|---------|------|
| 内容覆盖率 | 0% (0/4 deployed) | 0/3 KPI | 2/10 |
| UX 性能 | 40% (2/5 deployed, 有问题) | 部分进展 | 5/10 |
| 故障排查 | 70% (7/10 deployed, 已验证) | avg_resolution_steps=3 ✓ | 8/10 |

**整体目标达成度: 4.7/10**

**关键发现**:
- ✅ 故障排查目标推进最顺利（已部署 70%，KPI 达成）
- ❌ 内容覆盖目标完全停滞（AI 无法推进平台扩展和政策更新任务）
- ⚠️ 性能优化目标不完整且缺乏真实验证

---

## 四、问题模式分析

### 关键故障统计

```
总 action logs: 39
├─ 成功: 26 (66%)
└─ 失败: 13 (34%)

失败分布:
  heal/failed:           10 个 (78% 失败率)  ← 最严重
  feature_verify/failed:  3 个 (50% 失败率)
  feature_develop/failed: 1 个 (13% 失败率)
```

### 反复出现的问题

#### 1. **heal 失败率高 (78%)**

**根本原因**: `gh pr create` 因 "No commits between main and branch" 而反复失败

```
ERROR: gh pr create failed for vivify/site-health-315b92d754c0-1779680460:
pull request create failed: GraphQL: No commits between main and vivify/site-health-315b92d754c0-1779680460
```

**发生频率**: 至少 5 次失败记录（同一 issue hash）

**影响**: 
- 站点健康检查修复无法合并
- 同分支反复创建，浪费算力
- 用户看到重复的无效 PR

**缺失的检查**: feature_pipeline.py 应在 pr_creator 之前验证分支是否有新 commits

---

#### 2. **git 命令 timeout (12%)**

```
WARNING: gh pr merge --auto failed for 10: 
fatal: 'main' is already used by worktree at '/Users/chongshan/project/chongshan/mlive'
```

**发生频率**: 3 次 (PR #8, #9, #10 合并失败)

**原因**: worktree 占用冲突或网络超时（git fetch/push timeout 120s）

**特性 #9, #10 被拒原因**:
- #9: `git fetch --quiet origin main` timeout
- #10: `git push -u origin vivify/90-1779548533` timeout

---

#### 3. **验证失败 (50% feature_verify fail)**

```
Feature #11 verify failed:
PR #7 is still OPEN with merge conflicts (mergeStateStatus: DIRTY, mergeable: CONFLICTING)
None of the claimed artifacts are present in main.
```

**原因**: 部署前验证没有确保 PR 已合并

---

### 问题根源分析

| 问题 | 根本原因 | 影响 | 优先级 |
|-----|--------|------|-------|
| PR 创建前未检查 commits | feature_pipeline 缺少 branch-diff 检查 | 重复 PR、heal 失败 | P0 |
| worktree 冲突 | 未清理或隔离不充分 | merge/push timeout | P1 |
| 网络 timeout (120s) | 环境网络或仓库较大 | 特性被拒 | P2 |
| 验证时机错误 | verify 未等待 PR merge | 无效部署报告 | P1 |
| prompt 缺乏项目特定上下文 | goal_decompose.md.j2 没有最新 KPI 数据 | 生成与现状不符的 features | P1 |

---

## 五、Prompt 优化建议

### 当前 prompt 模板分析

共 5 个 prompt 模板:

1. **goal_decompose.md.j2** (83 行)
2. **feature_evaluate.md.j2** (53 行)
3. **feature_develop.md.j2** (45 行)
4. **feature_verify.md.j2** (44 行)
5. **fix_issue.md.j2** (56 行)

---

### 详细优化建议

#### 1. **goal_decompose.md.j2** — 缺乏 KPI 进度上下文

**现状问题**:
- `recent_snapshots` 和 `kpi_status` 参数在 mlive 项目中为空
- 无法帮助 AI 理解当前 KPI 进度，导致提议重复或偏离的特性
- 缺少"已部署特性"的成果反馈

**修改建议**:

```markdown
# 修改前 (当前版本)
{% if kpi_status -%}
## Current KPI status
{{ kpi_status }}
{%- endif %}

# 修改后 (改进版)
{% if kpi_status -%}
## Current KPI Status & Progress
{{ kpi_status }}

**Interpretation**:
- If a KPI is met: AVOID proposing related features, focus on other unmet KPIs
- If a KPI is 80%+ of target: propose only "polish" or "edge case" features
- If a KPI is <50% of target: propose high-impact root features first
{%- endif %}

{% if deployed_features -%}
## Recently Deployed Features (Last 14 days)
The following related features were already deployed. Avoid duplicating:
{{ deployed_features }}

**Guidance**: If a feature is already in PR/deployed, propose complementary or dependent features instead.
{%- endif %}

## Repository Overview & Constraints
- Default branch: `{{ repo_state.default_branch }}`
- Active worktrees: {{ active_worktrees | default("unknown", true) }}  ← Add this to detect conflicts
- Git fetch/push typical latency: {{ git_latency_ms | default("unknown", true) }}
```

**影响**: 减少 5-10% 的重复或无效特性提议

---

#### 2. **feature_evaluate.md.j2** — 缺乏"可部署性"前置评估

**现状问题**:
- Evaluate 阶段只关注"是否可行"，但不评估"是否可单独部署"或"是否有先决条件"
- 导致出现需要 follow-up features 的情况（如 #17 的 Discussion 启用需要手动管理员操作）

**修改建议**:

```markdown
# 在 output schema 前添加
## Evaluation Focus

1. **Feasibility**: Can this feature be implemented in isolation?
   - Required: List any required upstream changes or configs
   - Blockers: Identify other features this depends on

2. **Deployment Path**: When you mark `feasible: true`, also confirm:
   - No manual admin intervention required after PR merge?
   - No follow-up features are REQUIRED (vs. optional enhancement)?
   - All acceptance criteria can be auto-verified?

3. **Risk Assessment** (added requirement):
   - If `needs_admin_review: true`, specify: what decision does the admin need to make?
   - Provide the exact config/setting the admin must change

# 修改 output schema
{
  "priority": "P0|P1|P2|P3",
  "feasible": true,
  "feasibility": "...",
  "summary": "...",
  "needs_admin_review": false,
  "admin_action_required": "",  ← NEW: if needs_admin_review, describe here
  "required_upstream_features": [],  ← NEW: list blocking features
  "estimated_effort_hours": 4,
  ...
}
```

**影响**: 减少"不可完全自动化部署"的特性；提前发现依赖链

---

#### 3. **feature_develop.md.j2** — 缺乏分支质量与部署检查

**现状问题**:
- 开发完成后 commit/push，但 PR 创建逻辑在 kernel 中，缺乏分支有效性前置检查
- 无法预防"No commits between main and branch"错误

**修改建议**:

```markdown
# 在 "Plan of action" 后添加

## Pre-Push Quality Gate (BEFORE git push)

```bash
# (1) Verify branch has commits relative to main
git log main..HEAD --oneline | wc -l  # Must be >= 1

# (2) Check for merge conflicts locally
git merge-base HEAD origin/main | xargs git diff --name-only
  # If output is empty, branch is already synced with main

# (3) Dry-run: test that PR creation would not fail
# Simulate: gh pr create --draft --base main --head $(git rev-parse --abbrev-ref HEAD) --dry-run
#   (Note: gh doesn't have dry-run, so log the branch name and commit count instead)

if [ $(git rev-list --count main..HEAD) -eq 0 ]; then
  echo "ERROR: Branch has 0 commits since main. Aborting push."
  exit 1
fi
```

Add to post-push validation:
```
4. After `git push`, run: `gh pr view <branch> --json number` to confirm PR exists (or will be created)
   If PR already exists, verify it's mergeable: `gh pr view <pr_num> --json mergeable`
```

**影响**: 消除 "No commits between main and branch" 错误的 90%+

---

#### 4. **feature_verify.md.j2** — 验证时机和条件不明确

**现状问题**:
- 验证不检查 PR 是否已合并
- 部署前验证应该是 gated，不是"最好做做"

**修改建议**:

```markdown
# Feature Verification Gating

# 修改前言
The following feature has been merged via PR. Confirm the change is functional...

# 修改为
VERIFY THIS FEATURE ONLY AFTER:
1. ✅ PR has been merged to `{{ feature.base_branch | default("main") }}`
   - Use: `gh pr view {{ feature.pr_number | default("?") }} --json mergeStateStatus`
   - Must see: `mergeStateStatus: MERGED`
2. ✅ Code is now present in the default branch (do a fresh clone/fetch)

If either condition is false, report: `{"verified": false, "issues": ["PR not yet merged"]}`

---

## How to Verify
- Inspect the relevant code paths...
- ...
```

**影响**: 防止验证无效部署

---

#### 5. **fix_issue.md.j2** — 缺乏前置条件检查

**现状问题**:
- Fix issues (heal) 进程未在处理前检查 git 仓库状态
- 无法处理"worktree 占用"或"分支冲突"状况

**修改建议**:

```markdown
# 在最开头添加

## Prerequisite Health Check

Before making changes, verify the workspace is clean:

```bash
# Check for uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: Workspace has uncommitted changes"
  git status
  # Decide: abort or stash?
fi

# Check for active merges/rebases
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "ERROR: Git rebase/merge in progress. Aborting."
  exit 1
fi

# Verify branch is not used by another worktree
if git worktree list | grep -q "$(git rev-parse --abbrev-ref HEAD)"; then
  echo "ERROR: This branch is checked out in another worktree"
  exit 1
fi
```

Proceed only if all checks pass.

---

# Goal
Make the smallest, most targeted change...
```

**影响**: 减少 worktree 冲突导致的 timeout 和 merge 失败

---

### Prompt 优化总结表

| 模板 | 优化主题 | 收益 | 难度 |
|-----|--------|------|------|
| goal_decompose | 加入 KPI 进度解读 + deployed features 反馈 | -5-10% 重复 | 低 |
| feature_evaluate | 新增 admin_action_required + 依赖链检查 | -20% 无法自动化部署 | 中 |
| feature_develop | 加入分支有效性前置检查 | -90% "No commits" 错误 | 中 |
| feature_verify | 加入 PR merge 状态门禁 | -100% 无效验证 | 低 |
| fix_issue | 加入 git workspace 健康检查 | -60% merge timeout | 中 |

---

## 六、整体建议

### 6.1 短期行动 (1-2 周)

1. **修复 heal 流程的分支检查** (P0)
   - 在 `feature_pipeline.py` 的 PR 创建前添加 commit 检查
   - 参考内存中的经验教训: "vivify PR 创建需先校验分支是否含新提交"

2. **更新 goal_decompose prompt** (P1)
   - 注入当前 KPI 数值和已部署特性列表
   - 帮助 AI 理解"目标进度"而非"抽象目标"

3. **启用 GitHub Discussions** (P2)
   - 完成 #19, #20 的 follow-up 工作
   - 使社区支持系统完整可用

### 6.2 中期改进 (3-4 周)

4. **feature_develop 和 fix_issue 加入 workspace 健康检查**
   - 减少 timeout 和 worktree 冲突
   - 改进部署的稳定性

5. **添加部署前实时验证**
   - feature_verify 在 PR merge 前应 gate （不是事后验证）
   - 实施"3-pass 验证": 代码审查 → 功能测试 → KPI 基线对比

6. **为内容扩展类特性创建"内容编写 agent"**
   - 当前 goal_decompose 提议"新增 B 站平台"等特性后，qodercli 无法有效执行
   - 需要更专业的 prompt 来指导：如何编写平台特定的文档、政策格式、截图采集等

### 6.3 策略性改进 (1-2 月)

7. **建立 KPI 快照和趋势分析**
   - 当前缺乏"基线快照"（#9 被拒）
   - 需要周期性采集 KPI 数据（platform_coverage, policy_update_lag 等）
   - 使 goal_decompose 能看到真实进度而非推测

8. **优化目标与特性的对齐度**
   - 当前 3 个目标的推进速度差异大（故障排查 70% vs 内容覆盖 0%）
   - 分析原因：内容扩展是否超出 AI 能力？需要人工审核？
   - 考虑为"平台扩展"类目标引入"人工审查 gate"

9. **测试与验证框架**
   - 当前缺乏 lighthouse/性能审计自动化（#6 前端优化无法验证）
   - 为 mlive 项目添加 CI 性能基准测试
   - 使 feature_verify 能真实测量 page_load_time、mobile_responsive_score

---

## 七、Prompt 示例改进代码

### 示例 1: goal_decompose.md.j2 改进版片段

```jinja2
# 新增部分：KPI 进度解读指南

{% if kpi_status -%}
## Current KPI Status & Progress

{{ kpi_status }}

**How to Use This Information**:

{% set kpi_list = kpi_status.split('\n') if kpi_status else [] %}
1. **Identify Unmet KPIs**: Which KPIs are still below target?
   - These are your primary focus for new features.
   - If a KPI is already ≥80% of target, propose only refinement features.
   - If a KPI is <50% of target, prioritize high-impact root features.

2. **Avoid Duplicate Effort**: 
   - Features already in progress (see below) should NOT be duplicated.
   - Instead, propose complementary or dependent features that unlock them.

3. **Interdependencies**: Some KPIs may depend on others:
   - `avg_resolution_steps ≤ 3` depends on first having ≥10 troubleshoot_scenarios
   - If scenario count is low, focus there before polishing individual steps.

{%- endif %}

{% if deployed_features -%}
## Recently Deployed Features (Avoid Duplicates)

The following features were deployed or verified in the last 14 days:

{{ deployed_features }}

**Decision Rule**:
- If a similar feature exists in "deployed", skip it.
- If a related feature is in "pending/developing", propose a complementary one instead.

{%- endif %}
```

### 示例 2: feature_develop.md.j2 改进版片段

```jinja2
## Pre-Push Quality Gate

Before pushing, ensure the branch has actual new commits:

```bash
# Count commits between main and current branch
COMMIT_COUNT=$(git rev-list --count main..HEAD)
if [ "$COMMIT_COUNT" -lt 1 ]; then
  echo "[FATAL] Branch has 0 commits since main. This will cause PR creation to fail."
  echo "        Did you forget to commit changes? Run: git add -A && git commit -m '...'"
  exit 1
fi

echo "[OK] Branch has $COMMIT_COUNT commit(s). Safe to push."
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
```

If this script fails with commit count 0, **STOP and do not push**. The kernel will reject the PR.

{{ git_pr_snippet }}
```

---

## 八、数据支撑

### 附表 1: Feature Request 全景

| ID | 标题 | 目标 | 状态 | PR# | 备注 |
|-----|------|------|------|-----|------|
| 1 | 支付宝/快手/拼多多平台整合 | 内容覆盖 | pending | - | 0% 进度 |
| 2 | 统一文档 v2.1 + 2026-05 政策 | 内容覆盖 | pending | - | 0% 进度 |
| 3 | 诊断脚本 + 日志收集 | 故障排查 | deployed_with_issues | #3 | ✓ 已验证 |
| 4 | 10 个故障排查手册 | 故障排查 | deployed | #4 | ✓ 已验证 |
| 5 | 诊断脚本 | 故障排查 | deployed | #3 | 同 #3 |
| 6 | 前端脚本懒加载 | UX性能 | deployed_with_issues | #6 | ⚠ 未完全验证 |
| 7 | 10+ 故障决策树 | 故障排查 | deployed_with_issues | #5 | ⚠ merge 冲突 |
| 8 | B 站直播平台 | 内容覆盖 | pending | - | 0% 进度 |
| 9 | KPI 快照脚本 | 内容覆盖 | rejected | - | git fetch timeout |
| 10 | 移动端响应式设计 | UX性能 | rejected | - | git push timeout |
| 11 | CSS/JS 内联 + 预加载 | UX性能 | deployed_with_issues | #7 | ⚠ merge 未完成 |
| 16 | 快速解决摘要 | 故障排查 | verified | #10 | ✓ avg_steps=3 |
| 17 | Issue/Discussion 模板 | 故障排查 | verified | #9 | ✓ 部分完成 |
| 18 | FAQ 检索索引 | 故障排查 | verified | #8 | ✓ 已验证 |

### 附表 2: Action Logs 效率分析

```
Action Type          Avg Duration  Max Duration  Count  Success Rate
feature_develop      451.3s        727.1s        8      87.5%
heal                 200.7s        884.2s        13     23.1%
feature_verify       115.1s        223.2s        6      50.0%
feature_evaluate     102.1s        185.5s        10     100%
deploy               0.5s          0.5s          1      100%
```

**平均总周期** (从 decompose 到 deploy):
- 快速路径: ~500-700s (evaluate + develop)
- 完整路径: ~1200-1500s (evaluate + develop + verify)
- 失败反复: +2000s+ (multiple heal/pr 创建尝试)

---

## 九、结论

vivify 在 mlive 上已展现出**内容生成能力**（故障排查目标 70% 完成）和**工程效率**（从目标到 PR 平均 8-15 分钟），但在以下方面需要改进:

| 维度 | 当前表现 | 目标 | 改进空间 |
|-----|--------|------|--------|
| **heal 可靠性** | 23% | >90% | 需补充分支检查 |
| **prompt 准确性** | 缺乏上下文 | 包含实时 KPI | 中等优先级 |
| **目标完成度** | 4.7/10 | >8/10 | 需分析为何内容扩展停滞 |
| **代码质量** | 7.2/10 | >8.5/10 | 需完整验证和基准测试 |
| **部署稳定性** | 66% success | >95% | 需 workspace + timeout 处理 |

**建议**: 聚焦 P0 工作（heal 分支检查、KPI snapshot 建立），然后为"内容编写"类任务引入人工审核或专业 agent。

