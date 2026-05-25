# vivify Prompt 优化指南 — mlive 项目评估

> 本指南基于 mlive 项目 11 个已合并 PR 的分析，提供 vivify 5 个 prompt 模板的具体优化方案

**相关文档**: 
- 完整评估: `EVALUATION_REPORT_MLIVE_20260525.md`
- 执行摘要: `EVAL_SUMMARY.md`

---

## 目录

1. [prompt 位置](#prompt-位置)
2. [优化优先级](#优化优先级)
3. [具体修改方案](#具体修改方案)
4. [验证方法](#验证方法)

---

## Prompt 位置

所有 prompt 模板均位于:
```
/Users/chongshan/project/chongshan/vivify/vivify/agents/prompts/templates/
```

| 文件名 | 用途 | 行数 | 优先级 |
|--------|------|------|-------|
| goal_decompose.md.j2 | 目标分解 → 特性提议 | 83 | P1 |
| feature_evaluate.md.j2 | 特性可行性评估 | 53 | P2 |
| feature_develop.md.j2 | 代码开发执行 | 45 | P0 |
| feature_verify.md.j2 | 部署后验证 | 44 | P1 |
| fix_issue.md.j2 | issue 修复执行 | 56 | P1 |

---

## 优化优先级

### 🔴 P0: 立即修复

**feature_develop.md.j2** — 分支有效性检查  
- **问题**: 5+ 次 "No commits between main and branch" 失败
- **影响**: heal 流程 77% 失败率
- **修复工作量**: 1 小时
- **预期收益**: 消除 90%+ 此类错误

---

### 🟠 P1: 本周内修复

1. **goal_decompose.md.j2** — KPI 进度指导  
   - **问题**: 看不到已部署特性，导致重复提议
   - **工作量**: 2 小时
   - **收益**: 减少 5-10% 重复特性

2. **feature_verify.md.j2** — PR merge 状态检查  
   - **问题**: 验证时 PR 可能未合并，导致无效报告
   - **工作量**: 1 小时
   - **收益**: 防止无效部署记录

3. **fix_issue.md.j2** — workspace 健康检查  
   - **问题**: 3 个特性因 worktree 冲突被拒
   - **工作量**: 1.5 小时
   - **收益**: 减少 60% timeout 失败

---

### 🟡 P2: 后续完善

**feature_evaluate.md.j2** — 自动化可部署性评估  
- **问题**: 提议的特性有时需要人工 follow-up
- **工作量**: 3 小时
- **收益**: 提前发现依赖链

---

## 具体修改方案

### P0: feature_develop.md.j2

**当前第 34-40 行**:
```jinja2
## Plan of action
1. Read enough of the codebase to understand the affected areas.
2. Implement the change end-to-end (code + tests + minimal docs).
3. Run the project's tests / linters; iterate until they pass.
4. `git add -A && git commit -m "auto-heal: <one-line summary>"`.
5. `git push -u origin "$(git rev-parse --abbrev-ref HEAD)"`.

{{ git_pr_snippet }}
```

**修改为**:
```jinja2
## Workspace Readiness

Before starting work, verify the workspace is clean:

```bash
# (1) No uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
  echo "[ERROR] Workspace has uncommitted changes. Clean first."
  exit 1
fi

# (2) No active merge/rebase
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "[ERROR] Git rebase/merge in progress."
  exit 1
fi

# (3) Branch not used by another worktree
if git worktree list | grep -qE "$(git rev-parse --abbrev-ref HEAD)"; then
  echo "[ERROR] This branch is checked out elsewhere."
  exit 1
fi
```

## Plan of action
1. Read enough of the codebase to understand the affected areas.
2. Implement the change end-to-end (code + tests + minimal docs).
3. Run the project's tests / linters; iterate until they pass.
4. `git add -A && git commit -m "auto-heal: <one-line summary>"`.

## Pre-Push Validation (CRITICAL)

**Before** `git push`, run:

```bash
# MUST have at least 1 commit since main
COMMIT_COUNT=$(git rev-list --count main..HEAD)
if [ "$COMMIT_COUNT" -lt 1 ]; then
  echo "[FATAL] Branch has 0 commits since main."
  echo "        This will cause 'No commits between main and branch' error."
  echo "        Verify: git log main..HEAD --oneline"
  exit 1
fi

echo "[OK] Branch has $COMMIT_COUNT commit(s). Safe to push."
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
```

**Why this matters**: The kernel's PR creator checks for commits before calling `gh pr create`. 
If this fails, the PR will be rejected and the branch will be retried indefinitely.

{{ git_pr_snippet }}
```

**备注**:
- 此修改同样适用于 fix_issue.md.j2（第 35-40 行）
- 无需修改调用代码，完全通过 prompt 指导实现

---

### P1(a): goal_decompose.md.j2

**当前第 24-28 行**:
```jinja2
{% if kpi_status -%}
## Current KPI status
{{ kpi_status }}

{% endif -%}
```

**修改为**:
```jinja2
{% if kpi_status -%}
## Current KPI Status & Progress Guide

{{ kpi_status }}

**How to interpret and use this:**

1. **Identify which KPIs are unmet** (the target for your features):
   - If KPI < 50% of target: Propose 2-3 high-impact root features
   - If KPI ≥ 50% but < 80%: Propose complementary features to push it over the line
   - If KPI ≥ 80%: Propose polish/edge-case features only

2. **Avoid duplicate effort**:
   - See the deployed features list below
   - Do NOT propose a feature if similar work is already in flight
   - Instead, propose dependent or complementary features

3. **Watch for interdependencies**:
   - Example: `avg_resolution_steps ≤ 3` requires first having ≥10 troubleshoot_scenarios
   - Identify and prioritize accordingly

{%- endif %}

{% if deployed_features -%}
## Recently Deployed/Verified Features (Last 30 days)

The following features were recently deployed, verified, or are actively being developed:

{{ deployed_features }}

**Decision rule**: 
- If you see a similar feature in this list, SKIP it
- If you see a partial feature (e.g., "Quick Fix summaries" without "searchable index"), 
  consider proposing the complementary piece

{%- endif %}
```

**需要修改的调用代码** (`builders.py` line 123-131):
```python
# 当前:
def build_goal_decompose(
    goal: Goal,
    *,
    repo_state: RepoState,
    open_features: Sequence[FeatureRequest] = (),
    recent_snapshots: str = "",
    kpi_status: str = "",
    max_features: int = 3,
) -> str:

# 修改为:
def build_goal_decompose(
    goal: Goal,
    *,
    repo_state: RepoState,
    open_features: Sequence[FeatureRequest] = (),
    recent_snapshots: str = "",
    kpi_status: str = "",
    deployed_features: str = "",  # NEW
    max_features: int = 3,
) -> str:
```

**生成 deployed_features 的逻辑** (在 kernel/loop.py 或 goals/decomposer.py 中):
```python
def _format_deployed_features(storage, limit=20) -> str:
    """Get recently deployed/verified features from last 30 days."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    
    deployed = storage.list_features(status="deployed", limit=limit)
    verified = storage.list_features(status="verified", limit=limit)
    
    lines = []
    for fr in deployed + verified:
        if fr.updated_at > cutoff:
            status_mark = "✓" if fr.status == "verified" else "→"
            lines.append(f"- {status_mark} #{fr.id} [{fr.status}] {fr.title}")
    
    return "\n".join(lines) if lines else "(none)"
```

---

### P1(b): feature_verify.md.j2

**当前第 5-6 行**:
```jinja2
# auto-heal feature verification

The following feature has been merged via PR. Confirm the change is functional...
```

**修改为**:
```jinja2
# auto-heal feature verification

⚠️ **VERIFY THIS ONLY IF PR IS MERGED**

Before running verification:

```bash
# Check PR merge status
gh pr view {{ feature.pr_number }} --json mergeStateStatus

# Must see: mergeStateStatus: "MERGED"
# If output shows "OPEN" or "DIRTY", STOP and report:
# {"verified": false, "issues": ["PR not yet merged - cannot verify"]}
```

The following feature should have been merged via PR. Confirm the change is functional...
```

---

### P1(c): fix_issue.md.j2

**在第 35-40 行前插入**:
```jinja2
## Pre-Work Workspace Check

```bash
# (1) Verify no uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
  echo "[WARN] Uncommitted changes detected:"
  git status --short
  # Decide: stash or abort?
fi

# (2) Check git health
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "[FATAL] Git rebase/merge in progress. Cannot proceed."
  exit 1
fi

# (3) Verify worktree is not locked elsewhere
if git worktree list | grep -qE "$(git rev-parse --abbrev-ref HEAD)"; then
  echo "[FATAL] Branch is locked by another worktree."
  git worktree list
  exit 1
fi

echo "[OK] Workspace is clean and ready."
```

---

### P2: feature_evaluate.md.j2

**当前第 32-49 行**:
```jinja2
## Output (strict)

End your response with a fenced ```json``` block matching this schema:

```json
{
  "priority": "P0|P1|P2|P3",
  "feasible": true,
  "feasibility": "1-2 sentences on whether the change is realistic this round",
  ...
}
```

Set `needs_admin_review: true` if the change is large, ambiguous...
```

**修改为**:
```jinja2
## Feasibility Gate: Deployment Independence

Before marking `feasible: true`, confirm:

1. **Can this feature be deployed independently?**
   - Or does it require a follow-up feature first?
   - Example: "Enable GitHub Discussions" (feature #17) required admin to enable it in repo settings

2. **Any manual steps after PR merge?**
   - If yes, specify exactly what in `admin_actions_required`
   - Example: "Run: `npm run build && npm run deploy`"

3. **Any blocking upstream features?**
   - List them in `blocked_by` so the kernel can prioritize

---

## Output (strict)

End your response with a fenced ```json``` block matching this schema:

```json
{
  "priority": "P0|P1|P2|P3",
  "feasible": true,
  "feasibility": "1-2 sentences on whether the change is realistic this round",
  "summary": "1-3 sentence executive summary",
  "needs_admin_review": false,
  "admin_actions_required": "",  // NEW: if needs_admin_review=true, describe here
  "blocked_by": [],              // NEW: list feature IDs if any
  "estimated_effort_hours": 4,
  "affected_files_count": 6,
  "technical_complexity": "low|medium|high",
  "risks": ["short risk #1"],
  "implementation_approach": "concrete step-by-step plan"
}
```
```

---

## 验证方法

### 1. 修改后的 prompt 测试

```bash
cd /Users/chongshan/project/chongshan/vivify

# 测试 goal_decompose 的新增字段
python3 -c "
from vivify.agents.prompts import builders
from vivify.models.feature import FeatureRequest

prompt = builders.build_goal_decompose(
    goal=...,
    deployed_features='- ✓ #4 [verified] 故障手册\n- → #6 [deployed] 脚本懒加载'
)
print('[OK] deployed_features injected' if 'Recently Deployed' in prompt else '[FAIL]')
"

# 测试 feature_develop 的分支检查提示
python3 -c "
from vivify.agents.prompts import builders

prompt = builders.build_feature_develop(feature=...)
print('[OK] Pre-Push Validation present' if 'Pre-Push Validation' in prompt else '[FAIL]')
"
```

### 2. mlive 项目上的 E2E 测试

修改后，在 mlive 项目上运行一轮 decompose:

```bash
cd /Users/chongshan/project/chongshan/mlive

# 运行 goal decomposition
vivify goals decompose

# 预期: 新提议的特性不重复、符合当前 KPI 状态
# 检查: 是否出现重复的"诊断脚本"、"快速解决摘要"等已部署特性
```

### 3. 监控关键指标

部署后持续监控:

| 指标 | 当前 | 目标 | 检查方法 |
|-----|-----|------|--------|
| heal 成功率 | 23% | >80% | `grep "heal/success" .vivify/logs/*.log \| wc -l` |
| PR 创建失败 | 5+ | <1 | `grep "No commits between" .vivify/logs/*.log` |
| feature_verify 成功率 | 50% | >90% | `sqlite3 .vivify/state.db "SELECT status, COUNT(*) FROM action_logs WHERE action_type='feature_verify' GROUP BY status"` |

---

## 总结

| Prompt | 修改项 | 工作量 | 预期收益 |
|--------|-------|-------|--------|
| feature_develop | Pre-push commit check | 1h | -90% "No commits" 错误 |
| goal_decompose | KPI 进度 + deployed features | 2h | -5-10% 重复特性 |
| feature_verify | PR merge 状态门禁 | 1h | -100% 无效验证 |
| fix_issue | workspace 健康检查 | 1.5h | -60% timeout |
| feature_evaluate | 可部署性评估 | 3h | 提前发现依赖 |

**总工作量**: ~8.5 小时  
**预期 ROI**: heal 流程从 23% → 80%+, 整体成功率从 66% → 92%+

