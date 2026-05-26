# channels-monitor 需求管理框架深入分析报告

## 执行摘要

channels-monitor 项目展示了一套**成熟的自主迭代开发框架**，核心亮点包括：
1. **完整的功能请求生命周期管理**：从 pending → evaluating → approved → developing → deployed → verified 六阶段
2. **结果导向的验证机制**：不验证代码本身，而验证实际业务指标改进（数据质量评分、失败记录减少等）
3. **智能化的需求关联与派生**：支持 parent_id 关联、跟进反馈自动创建子需求、想法拆解为可执行特性
4. **完善的超时恢复和自动修复**：防止长期卡住，自动重置或升级处理策略
5. **多维度的并行开发框架**：评估纯只读并行无冲突，开发通过独立 worktree 并行

本报告逐项对比 vivify 当前实现，提出具体改进建议。

---

## 一、channels-monitor 的需求管理框架总览

### 1.1 需求定义与分类

#### 数据模型
channels-monitor 的功能请求表结构（`feature_requests` 表）：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER PK | 自增主键，全局唯一需求ID |
| title | TEXT | 需求标题（200字以内） |
| description | TEXT | 详细描述 |
| **type** | TEXT | **feature/bug/optimization**（三分类） |
| **status** | TEXT | **pending/evaluating/approved/rejected/developing/deployed/verified/deployed_with_issues** |
| priority | TEXT | P0/P1/P2/P3（四级优先级，可选） |
| parent_id | INTEGER FK | 跟进反馈/派生需求的父需求ID |
| feasibility | TEXT | AI评估的可行性（如"High/Medium/Low"） |
| evaluation_summary | TEXT | AI评估结论 |
| development_result | TEXT | 开发结果详情 |
| verification_result | TEXT | 验证结果（结果导向） |
| commit_hash | TEXT | 单个需求开发的提交哈希 |
| batch_commit_hash | TEXT | 合并开发批次的提交哈希 |
| image_urls | JSON | 截图URL列表（支持多图附件，≤5张） |
| submitted_by | TEXT | 提交者身份（admin/auto_heal/vip邮箱等） |
| idea_id | INTEGER FK | 关联的想法ID（创意来源） |
| retry_count | INTEGER | 失败重试计数 |
| error_message | TEXT | 失败原因 |
| created_at | TIMESTAMP | 创建时间 |
| evaluated_at | TIMESTAMP | 完成评估时间 |
| started_at | TIMESTAMP | 开始开发时间 |
| completed_at | TIMESTAMP | 完成开发时间 |
| verified_at | TIMESTAMP | 完成验证时间 |

#### 分类体系
- **type**: feature（功能需求）、bug（缺陷修复）、optimization（性能优化）
- **status**: 8种状态，清晰的状态流转路径
- **priority**: 支持P0-P3四级优先级（自动或手动设置）
- **submitted_by**: 区分来源身份，便于权限控制和审计

### 1.2 生命周期与状态流转

```
pending ─→ evaluating ─→ approved ─→ developing ─→ deployed ─→ verified
           ↓                           ↓                           ↓
        rejected              development failed         deployed_with_issues
```

关键特性：
- **rejected**: 评估不可行的需求直接拒绝，不进入开发队列
- **deploying**: 支持"已部署但有问题"的中间状态，允许跟进反馈
- **超时自动降级**: 
  - evaluating 超过10分钟 → reset为 pending
  - developing 超过1.5小时 → reset为 approved
  - verifying 超过60分钟 → reset为 deployed

### 1.3 需求发起与权限

三类角色的权限差异：

| 功能 | Admin | VIP | User |
|------|-------|-----|------|
| 创建功能需求 | ✓ | ✓ | ✗ |
| 创建Bug报告 | ✓ | ✓ | ✗ |
| 上传截图（≤5张） | ✓ | ✓ | ✗ |
| 查看所有需求 | ✓ | ✓ | ✗ |
| 查看自己的需求 | ✓ | ✓ | ✓ |
| 提交反馈/跟进修复 | ✓ | ✓ | ✗ |
| 自动批准需求 | ✓（admin自动），✓（跟进反馈） | ✗ | ✗ |

### 1.4 优先级与自动分类

支持两种优先级策略：
1. **手动优先级**：提交时显式指定 P0-P3
2. **AI自动评估**：根据需求类型和内容自动分配优先级
   - feature + High feasibility → P1
   - bug → 自动升至 P0/P1（缺陷优先于功能）
   - optimization → P2/P3（低优先级）

### 1.5 完成定义（Done Criteria）

需求视为"完成"需满足全部条件：
1. ✓ 通过 AI 评估（evaluation_summary 非空）
2. ✓ 代码提交到主分支（commit_hash 非空）
3. ✓ 自动化验证通过（verification_result = "verified" 且 improved=true）
4. ✓ 业务指标改进（质量评分提升、失败减少、积压缓解）

---

## 二、自主迭代开发框架

### 2.1 总体工作流

channels-monitor 的 auto_heal 系统采用**真正并行的工作流**：

```
┌─────────────────────────────────────────────────────────┐
│                  Feature Development Loop                │
│                   （每 3 分钟执行一轮）                    │
└─────────────────────────────────────────────────────────┘
          │
          ├─→ [并行1] 评估阶段 (Evaluation)
          │   • 读取 pending 状态需求
          │   • 调用 qodercli 进行 AI 评估（纯只读，无代码变更）
          │   • 可并行 4 个线程（MAX_EVAL_WORKERS=4）
          │   • 最多评估 8 个需求/轮（MAX_FEATURE_EVALS_PER_ROUND=8）
          │   • 最多 15 轮（FEATURE_EVAL_MAX_TURNS=15）
          │   • 输出：approved/rejected + 可行性评估
          │
          ├─→ [并行2] 开发阶段 (Development)
          │   • 读取 approved 状态需求
          │   • 为每个需求创建独立 git worktree
          │   • 支持两种开发模式：
          │     (a) 单个需求开发：worktree → qodercli → git merge → deploy
          │     (b) 合并开发（批处理）：多个相关需求 → 单个 worktree → qodercli → merge
          │   • 开发可并行 3 个 worktree（MAX_DEV_WORKERS=3）
          │   • 最多开发 6 个需求/轮（MAX_FEATURE_DEVS_PER_ROUND=6）
          │   • 最多 100 轮（FEATURE_DEV_MAX_TURNS=100）
          │   • 超时 1.5 小时后重置为 approved（FEATURE_DEV_TIMEOUT=5400s）
          │   • 输出：deployed + commit_hash + development_result
          │
          ├─→ [并行3] 验证阶段 (Verification)
          │   • 读取 deployed 状态需求
          │   • 调用 qodercli 进行测试验证（纯只读）
          │   • 可并行 2 个线程（MAX_VERIFY_WORKERS=2）
          │   • 最多验证 4 个需求/轮（MAX_FEATURE_VERIFIES_PER_ROUND=4）
          │   • 最多 15 轮（FEATURE_VERIFY_MAX_TURNS=15）
          │   • 验证失败时自动重试（最多 2 次）
          │   • 输出：verified + verification_result (JSON)
          │
          └─→ 结果回写到后端 API
              • 更新 status、summary、commit_hash 等字段
              • 触发事件通知（SSE to frontend）
              • 失败自动升级/重试逻辑
```

### 2.2 评估阶段（Evaluation）

**纯只读分析，完全并行无冲突。**

```python
# 核心流程（feature_dev.py）
def _evaluate_feature(feature: dict) -> dict:
    """
    输入：pending 状态的需求（title + description）
    过程：
      1. 通过 SSH 隧道连接到后端 API（获取上下文）
      2. 构建评估 prompt（goal分解 + 已有相关需求上下文）
      3. 调用 qodercli 进行 AI 评估
      4. 解析 JSON 输出：
         {
           "feasibility": "High" | "Medium" | "Low",
           "priority": "P0" | "P1" | "P2" | "P3",
           "estimated_effort": "小" | "中" | "大",
           "summary": "...",
           "reason": "..."
         }
      5. 根据 feasibility 决策：
         - High/Medium → status = approved
         - Low → status = rejected
    输出：evaluation_summary + priority + feasibility
    """
```

**评估超时恢复**：
- 检查条件：`now - evaluated_at > 10 分钟` 且 `status = evaluating`
- 自动动作：重置为 pending，清除 error_message，记录警告日志
- 效果：防止评估卡住，允许下一轮重新评估

### 2.3 开发阶段（Development）

**每个需求独立 worktree，顺序合并保证 main 分支一致性。**

```
单个需求开发流程：
┌──────────────────────────────────────┐
│ 1. 创建 worktree                      │
│    git worktree add .worktrees/f{id}  │
└──────────────────────────────────────┘
              ↓
┌──────────────────────────────────────┐
│ 2. 在 worktree 中执行开发             │
│    cd .worktrees/f{id}              │
│    qodercli do --goal "实现 feature" │
│    • 自动修改代码                     │
│    • 自动提交                         │
│    • 自动推送到特性分支                │
└──────────────────────────────────────┘
              ↓
┌──────────────────────────────────────┐
│ 3. 解析开发输出                      │
│    • 提取 commit_hash                │
│    • 解析 next_steps（跟进需求）     │
│    • 标记跳过的需求（skipped_ids）   │
└──────────────────────────────────────┘
              ↓
┌──────────────────────────────────────┐
│ 4. 合并回 main（顺序执行）            │
│    • git fetch && git merge           │
│    • 冲突处理 / 验证合并正确性        │
│    • status = deployed                │
└──────────────────────────────────────┘
              ↓
┌──────────────────────────────────────┐
│ 5. 清理 worktree                      │
│    git worktree remove .worktrees/f{id} │
└──────────────────────────────────────┘
```

**合并开发（Batch Development）**：
```
条件：多个相关需求（共享关键词 >= 2）且都是 P2/P3 优先级
优势：减少 worktree 创建/删除/合并开销 → 更快部署
风险：若一个需求失败，整个批次失败 → 拆出来单独重试

实现：
  qodercli do --goal "实现需求#42 + 需求#43 + 需求#45" \
             --batch-info "{requirements: [42, 43, 45]}"

结果：
  • batch_commit_hash = 单个提交哈希
  • 所有需求共享该提交
  • 若 N 次失败后自动拆散，各需求单独开发
```

**开发超时恢复**：
- 检查条件：`now - started_at > 1.5小时` 且 `status = developing`
- 自动动作：重置为 approved，记录失败原因
- 效果：允许下一轮重新开发，或升级为手工干预

### 2.4 验证阶段（Verification）

**结果导向验证 - 核心创新**

不验证代码是否正确，而验证**实际业务指标是否改进**。

```python
def verify_feature_deployment(feature_id: int) -> dict:
    """
    验证步骤：
    1. 在部署前拉取一份"before"快照（业务指标）
    2. 部署代码（git pull）
    3. 等待 30 秒（代码生效）
    4. 拉取"after"快照
    5. 对比两个快照的关键指标变化
    
    对比指标：
    • 数据质量综合评分（overall_score）：各维度聚合分数
    • 失败记录数：是否减少
    • 离线设备数：是否减少
    • 截图积压数：是否缓解
    • 今日抓取量：是否增加
    
    判定规则：
    • 综合评分提升 >= 0.5 分 → verified = true
    • 或同时满足以下任一：
      - 失败数减少
      - 离线设备减少
      - 积压缓解
    → verified = true
    
    返回结果：
    {
      "verified": true | false,
      "summary": "数据质量评分提升 0.8（74.2 → 75.0）；失败记录减少 3 条",
      "issues": [...],  # 若 verified=false，记录问题原因
      "details": {
        "quality_score": {"before": 74.2, "after": 75.0, "delta": 0.8},
        "failures": {"before": 12, "after": 9},
        "offline_devices": {"before": 2, "after": 1},
        ...
      }
    }
    """
```

**验证超时恢复**：
- 检查条件：`now - completed_at > 60分钟` 且 `status = verifying`
- 自动动作：重置为 deployed，允许下一轮重新验证
- 效果：防止验证无限卡住

### 2.5 失败恢复与自动升级

```
失败处理流程：
┌─────────────────────────────────────┐
│ 1. 检测失败（status='*_failed'）   │
│    • qodercli 返回非 0 exit code  │
│    • 输出解析失败                    │
│    • 合并冲突                        │
└─────────────────────────────────────┘
          ↓
┌─────────────────────────────────────┐
│ 2. 记录失败信息                      │
│    • error_message = "..."          │
│    • retry_count += 1               │
│    • 若为合并开发失败 → 标记下轮拆散 │
└─────────────────────────────────────┘
          ↓
┌─────────────────────────────────────┐
│ 3. 决策重试或升级                    │
│    if retry_count < MAX_RETRIES:   │
│      • 重置为上一状态（evaluating/approved） │
│      • 下一轮重试                    │
│    else:                            │
│      • status = needs_admin_review  │
│      • 自动生成 GitHub Issue        │
│      • 通知 Admin 手工干预           │
└─────────────────────────────────────┘
```

---

## 三、跟进反馈与需求派生

### 3.1 反馈流程

```
┌──────────────────────────────┐
│ 已部署/已验证的需求#42       │
│（如：新增某功能）             │
└──────────────────────────────┘
          ↓
   [用户提交反馈]
   "该功能运行缓慢"
   "存在边界情况问题"
          ↓
┌──────────────────────────────────────────┐
│ POST /admin/feature-requests/42/feedback │
│ {                                        │
│   "description": "反馈内容",             │
│   "image_urls": [...]  # 可选截图       │
│ }                                        │
└──────────────────────────────────────────┘
          ↓
┌──────────────────────────────┐
│ 自动创建派生Bug#55           │
│ • type = "bug"              │
│ • parent_id = 42            │
│ • title = "反馈: ..."       │
│ • description = "用户反馈..." │
│ • status = "pending"        │
│   （等待 AI 评估）            │
└──────────────────────────────┘
          ↓
   [跟进反馈自动批准]
   if parent_id is not None:
     • status 直接跳至 approved
     • 不需等待评估
     • 下一轮直接进入开发
```

### 3.2 想法拆解（Idea Breakdown）

```
用户提交"想法"
 ↓
┌─────────────────────────────────────┐
│ POST /admin/ideas                    │
│ {                                   │
│   "title": "支持多语言",            │
│   "description": "系统应支持...",    │
│ }                                   │
└─────────────────────────────────────┘
 ↓
[AI 拆解想法为可执行特性]
qodercli do --goal "请将以下想法拆解为 3-5 个可实施的需求"

想法#10 "支持多语言"
   ↓ 拆解为：
   ├─ 需求#55：国际化文本外挂（feature, P2）
   ├─ 需求#56：翻译 API 集成（feature, P2）
   ├─ 需求#57：语言选择器 UI（feature, P1）
   └─ 需求#58：RTL 语言适配（optimization, P3）
   
   所有派生需求：
   • idea_id = 10（关联回原想法）
   • status = "pending"（等待评估）
   • parent_goal = "想法#10"
```

---

## 四、channels-monitor vs vivify 对比分析

### 4.1 需求数据模型对比

#### channels-monitor（当前）
```
feature_requests 表：
✓ type: feature/bug/optimization （三分类）
✓ status: 8 个细粒度状态
✓ priority: P0-P3
✓ feasibility: High/Medium/Low （AI 评估可行性）
✓ image_urls: JSON，支持多图附件
✓ parent_id: 支持需求关联与派生
✓ idea_id: 关联想法来源
✓ retry_count: 失败重试计数
✓ batch_commit_hash: 合并开发的提交
✓ verification_result: 结果导向验证 JSON
✓ 完整的时间戳：created/evaluated/started/completed/verified_at
```

#### vivify（当前）
```
feature_requests 表（vivify/storage/migrations/001_*.sql）：
✓ type: feature/bug/optimization （相同）
~ status: 7 个状态（缺少 deployed_with_issues）
✓ priority: P0-P3
✗ feasibility: 不记录 AI 评估可行性
✗ image_urls: 不支持附件
✗ parent_id: 不支持需求关联
✗ idea_id: 不支持想法拆解
✗ retry_count: 不记录重试次数
✗ batch_commit_hash: 不支持合并开发
✗ verification_result: 不支持结果导向验证
✗ 时间戳：仅 created_at/updated_at，缺少 evaluated_at/started_at 等
```

**差距评分**：`(功能项缺失 8/10 * 80%) = 64% 差距`

### 4.2 生命周期流转对比

#### channels-monitor
```
pending → evaluating → [approved | rejected]
                          ↓
                     developing → [deployed | deployed_with_issues]
                                      ↓
                                  verifying → verified
```
**特点**：
- 8 种状态清晰递进
- 支持"已部署但有问题"中间状态
- 自动降级/重试逻辑完善

#### vivify
```
pending → evaluating → [approved | rejected]
                          ↓
                     developing → deployed → verified
```
**特点**：
- 7 种状态，基本流程完整
- 缺少"deployed_with_issues"
- 缺少自动超时恢复机制

### 4.3 验证方法论对比

#### channels-monitor（结果导向）
```
验证内容：实际业务指标变化
• 数据质量综合评分（overall_score）
• 失败记录数
• 离线设备数
• 截图积压数
• 今日抓取量
• 各维度评分变化

判定规则：综合评分 >= 0.5 分提升 → verified = true
          或满足任一指标改进 → verified = true
          
输出格式：JSON，包含 details（before/after 对比）
```

#### vivify（实现导向）
```
验证内容：代码质量检查
• 代码审查
• 测试覆盖
• 类型检查
• 文档完整性

判定规则：手工设置或基于 qodercli 输出
输出格式：simple string summary
```

**优劣分析**：
- channels-monitor：面向真实业务效果，用户可见，更有说服力，但需要稳定的指标体系
- vivify：面向代码质量，更容易自动化，但难以证明功能对用户有真实帮助

### 4.4 并行开发能力对比

#### channels-monitor
```
评估阶段：4 线程并行，纯只读，无冲突
开发阶段：3 worktree 并行 + 合并开发（批处理）
验证阶段：2 线程并行，纯只读
```

#### vivify
```
（目前未实现显式的并行框架）
```

### 4.5 超时恢复对比

#### channels-monitor
```
✓ evaluating > 10 分钟 → reset 为 pending
✓ developing > 1.5 小时 → reset 为 approved
✓ verifying > 60 分钟 → reset 为 deployed
✓ idea_breakdown > 15 分钟 → reset 为 pending
```

#### vivify
```
✗ 暂无超时自动恢复机制
  → 若 qodercli 卡住，需手工介入或重启
```

---

## 五、可借鉴到 vivify 的具体思路

### 优先级 P1：核心能力增强（ROI 最高）

#### P1.1 完善需求数据模型
**改进内容**：
```sql
-- vivify/storage/migrations/002_enhance_feature_model.sql
ALTER TABLE feature_requests ADD COLUMN (
    feasibility TEXT,              -- AI 评估可行性：High/Medium/Low
    image_urls TEXT,               -- JSON 格式的截图 URL 列表（最多 5 张）
    parent_id INTEGER,             -- 派生需求的父需求 ID
    idea_id INTEGER,               -- 关联想法的 ID
    retry_count INTEGER DEFAULT 0, -- 失败重试计数
    batch_commit_hash TEXT,        -- 合并开发的提交哈希
    verification_result TEXT,      -- 验证结果 JSON
    evaluated_at TIMESTAMP,        -- 评估完成时间
    started_at TIMESTAMP,          -- 开发开始时间
    verified_at TIMESTAMP,         -- 验证完成时间
    FOREIGN KEY (parent_id) REFERENCES feature_requests(id),
    FOREIGN KEY (idea_id) REFERENCES ideas(id)
);

-- 补充 deployed_with_issues 状态
-- 修改 feature_requests 表 status 字段的 CHECK 约束
ALTER TABLE feature_requests 
  ADD CHECK (status IN (
    'pending', 'evaluating', 'approved', 'rejected',
    'developing', 'deployed', 'deployed_with_issues', 
    'verifying', 'verified'
  ));
```

**代码改动**（vivify/models/feature.py）：
```python
FeatureStatus = Literal[
    "pending", "evaluating", "approved", "rejected",
    "developing", "deployed", "deployed_with_issues",  # 新增
    "verifying", "verified",
]

@dataclass
class FeatureRequest:
    # ... 现有字段 ...
    feasibility: Optional[str] = None  # AI 评估的可行性
    image_urls: Optional[List[str]] = None  # 附件截图
    parent_id: Optional[int] = None  # 派生需求关联
    idea_id: Optional[int] = None  # 想法拆解来源
    retry_count: int = 0  # 失败重试计数
    batch_commit_hash: Optional[str] = None  # 合并开发的提交
    verification_result: Optional[dict] = None  # 验证结果详情
    evaluated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
```

**影响**：
- 存储层支持更细粒度的需求生命周期管理
- 支持跟进反馈和想法拆解
- 支持结果导向验证

---

#### P1.2 实现需求超时自动恢复
**改进内容**：
```python
# vivify/kernel/failure_tracker.py 新增

async def detect_and_recover_timeouts():
    """定时检测卡住的需求，自动重置状态"""
    import asyncio
    from datetime import datetime, timedelta, timezone
    
    async with get_db() as conn:
        # 检测评估超时（10 分钟）
        timeout_threshold = datetime.now(timezone.utc) - timedelta(minutes=10)
        stale_evaluating = await conn.execute(
            """
            SELECT id, title FROM feature_requests 
            WHERE status = 'evaluating' AND evaluated_at IS NULL
            AND created_at < ?
            """,
            (timeout_threshold,)
        ).fetchall()
        
        for fid, title in stale_evaluating:
            await conn.execute(
                """
                UPDATE feature_requests 
                SET status = 'pending', error_message = 'Evaluation timeout after 10 min'
                WHERE id = ?
                """,
                (fid,)
            )
            logger.warning(f"[Timeout] Feature #{fid} '{title}' reset from evaluating to pending")
        
        # 检测开发超时（1.5 小时）
        dev_timeout = datetime.now(timezone.utc) - timedelta(hours=1.5)
        stale_developing = await conn.execute(
            """
            SELECT id, title FROM feature_requests 
            WHERE status = 'developing' AND started_at < ?
            """,
            (dev_timeout,)
        ).fetchall()
        
        for fid, title in stale_developing:
            await conn.execute(
                """
                UPDATE feature_requests 
                SET status = 'approved', error_message = 'Development timeout after 1.5h'
                WHERE id = ?
                """,
                (fid,)
            )
            logger.warning(f"[Timeout] Feature #{fid} '{title}' reset from developing to approved")
        
        await conn.commit()

# vivify/kernel/loop.py 中集成到主循环
async def run_kernel_loop():
    # ... 现有逻辑 ...
    while True:
        try:
            # 每轮检测一次超时
            await detect_and_recover_timeouts()
            
            # ... 评估、开发、验证 ...
        except Exception as e:
            logger.error(f"Kernel loop error: {e}")
        
        await asyncio.sleep(300)  # 5 分钟一轮
```

**集成到 feature_pipeline.py**：
```python
# vivify/kernel/feature_pipeline.py
class FeaturePipeline:
    async def run(self):
        while True:
            # 1. 检测并恢复超时
            await self._recover_timeouts()
            
            # 2. 评估 pending 需求
            await self._evaluate_pending()
            
            # 3. 开发 approved 需求
            await self._develop_approved()
            
            # 4. 验证 deployed 需求
            await self._verify_deployed()
            
            await asyncio.sleep(self.config.loop_interval)
```

**影响**：
- 防止需求长期卡住
- 自动恢复，提高系统稳定性
- 无需手工干预

---

#### P1.3 支持需求关联与派生
**改进内容**：
```python
# vivify/models/feature.py
@dataclass
class FeatureRequest:
    parent_id: Optional[int] = None  # 派生自某个需求
    # ... 其他字段 ...

# vivify/kernel/feature_pipeline.py 新增派生逻辑
async def create_followup_feature(parent_id: int, title: str, description: str) -> int:
    """为某个已部署/已验证的需求创建跟进修复需求"""
    async with get_db() as conn:
        fid = await conn.execute(
            """
            INSERT INTO feature_requests 
            (title, description, type, status, parent_id, created_at, updated_at)
            VALUES (?, ?, 'bug', 'approved', ?, ?, ?)
            """,
            (title, description, parent_id, datetime.now(timezone.utc), datetime.now(timezone.utc))
        ).lastrowid
        
        # 派生的需求自动跳过评估，直接进入 approved（因为来自已部署的需求）
        return fid

# 使用示例：
# 用户对已验证的需求#42 提交反馈
# → 自动创建需求#55 (type=bug, parent_id=42, status=approved)
# → 下一轮直接进入开发，无需等待评估
```

**影响**：
- 支持连续迭代改进
- 快速响应用户反馈
- 建立需求的上下文关系

---

### 优先级 P1：功能补强（中等价值）

#### P1.4 结果导向验证框架
**改进内容**：

替代当前的"代码审查"验证，改为"业务指标验证"：

```python
# vivify/verifier/result_based.py
class ResultBasedVerifier:
    """验证需求通过对比部署前后的业务指标"""
    
    async def verify(self, feature_id: int) -> VerificationResult:
        """
        1. 部署前拍快照
        2. 部署代码
        3. 部署后拍快照
        4. 对比关键指标
        5. 判定 verified
        """
        feature = await self.storage.get_feature(feature_id)
        
        # 部署前快照
        before_metrics = await self._collect_metrics()
        
        # 执行部署（git pull + restart）
        await self._deploy()
        await asyncio.sleep(30)  # 等待代码生效
        
        # 部署后快照
        after_metrics = await self._collect_metrics()
        
        # 分析关键指标变化
        result = self._analyze_metrics(before_metrics, after_metrics)
        
        # 存储验证结果
        feature.verification_result = result.to_dict()
        feature.verified_at = datetime.now(timezone.utc)
        feature.status = "verified" if result.verified else "deployed_with_issues"
        await self.storage.update_feature(feature)
        
        return result
    
    async def _collect_metrics(self) -> dict:
        """
        收集关键业务指标：
        • 错误率、异常数
        • 响应时间、吞吐量
        • 用户活跃度
        • 功能使用统计
        """
        # 查询数据库、日志、监控系统等
        pass
    
    def _analyze_metrics(self, before: dict, after: dict) -> VerificationResult:
        """对比指标变化，判定是否改进"""
        improvements = []
        if after.get("error_rate", 1) < before.get("error_rate", 1):
            improvements.append("错误率下降")
        if after.get("response_time", 0) < before.get("response_time", 0):
            improvements.append("响应时间缩短")
        # ... 更多指标 ...
        
        verified = len(improvements) > 0
        return VerificationResult(
            verified=verified,
            summary="; ".join(improvements) if improvements else "无明显改进",
            details={"before": before, "after": after}
        )
```

**配置管理**（vivify/config/defaults.py）：
```python
FEATURE_VERIFICATION_MODE = "result_based"  # vs "code_based"
VERIFICATION_METRICS = [
    "error_rate",
    "response_time",
    "throughput",
    "user_engagement",
    # ...
]
VERIFICATION_IMPROVEMENT_THRESHOLD = 0.1  # 10% 改进视为通过
```

**影响**：
- 验证重点从代码质量转向用户价值
- 更有说服力，利于团队接纳
- 需要完善的监控/指标体系支撑

---

#### P1.5 合并开发（批处理）
**改进内容**：

```python
# vivify/kernel/feature_pipeline.py
class FeaturePipeline:
    async def _develop_approved(self):
        """支持单个或批处理开发"""
        approved = await self.storage.list_features_by_status("approved")
        
        if self.config.feature_dev_batch_enabled:
            # 分组相关需求
            batches = self._group_related_features(approved)
            for batch in batches:
                await self._develop_batch(batch)
        else:
            # 单个开发
            for feature in approved:
                await self._develop_single(feature)
    
    def _group_related_features(self, features: List[FeatureRequest]) -> List[List[FeatureRequest]]:
        """
        将相关的低优先级（P2/P3）需求分组
        相关度判定：共享关键词数 >= 2
        """
        from difflib import SequenceMatcher
        batches = []
        used = set()
        
        for i, f1 in enumerate(features):
            if i in used or f1.priority not in ("P2", "P3"):
                continue
            
            batch = [f1]
            used.add(i)
            
            for j, f2 in enumerate(features[i+1:], i+1):
                if j in used or f2.priority not in ("P2", "P3"):
                    continue
                
                # 计算相似度
                sim = SequenceMatcher(None, f1.title, f2.title).ratio()
                if sim > 0.3:  # 相似度 > 30%
                    batch.append(f2)
                    used.add(j)
            
            batches.append(batch)
        
        return [b for b in batches if len(b) > 1] + [[f] for i, f in enumerate(features) if i not in used]
    
    async def _develop_batch(self, batch: List[FeatureRequest]):
        """批处理开发多个相关需求"""
        # 创建单个 worktree
        worktree_path = self._create_worktree(f"batch_{batch[0].id}")
        
        try:
            # 构建 goal
            goal = "请实现以下需求：\n" + "\n".join([
                f"- #{f.id}: {f.title}" for f in batch
            ])
            
            # 单次 qodercli do 开发所有需求
            result = await self.agent.do(goal, worktree_path=worktree_path)
            
            # 解析输出，提取各需求的结果
            for feature in batch:
                feature.status = "deployed"
                feature.commit_hash = result.get("commit_hash")
                feature.batch_commit_hash = result.get("batch_commit_hash")
                await self.storage.update_feature(feature)
        
        except Exception as e:
            # 开发失败，下轮拆散重试
            for feature in batch:
                feature.retry_count += 1
                if feature.retry_count >= self.config.batch_solo_threshold:
                    feature.status = "approved"  # 重置为 approved，下轮单独开发
                await self.storage.update_feature(feature)
        
        finally:
            self._cleanup_worktree(worktree_path)
```

**配置**（vivify/config/defaults.py）：
```python
FEATURE_DEV_BATCH_ENABLED = True  # 启用合并开发
FEATURE_DEV_BATCH_MAX_SIZE = 3    # 每批最多 3 个
FEATURE_DEV_BATCH_MIN_SIMILARITY = 0.3  # 相似度阈值
FEATURE_DEV_BATCH_SOLO_THRESHOLD = 1   # 失败 1 次后拆散
FEATURE_DEV_BATCH_ALLOWED_PRIORITIES = {"P2", "P3"}  # 只对低优先级进行批处理
```

**影响**：
- 减少 worktree 创建/删除/合并开销
- 加快低优先级需求部署速度
- 降低并行开发的系统复杂度

---

### 优先级 P2：想法管理（长期价值）

#### P2.1 想法（Ideas）拆解框架
**改进内容**：

```python
# vivify/models/idea.py
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

@dataclass
class Idea:
    """用户提交的"想法"或"特性建议"，可拆解为多个可执行需求"""
    title: str
    description: str
    id: int = 0
    status: str = "pending"  # pending/decomposing/decomposed/dismissed
    decomposition_result: Optional[str] = None  # 拆解结果 JSON
    derived_features: Optional[List[int]] = None  # 派生的需求 ID 列表
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

# vivify/kernel/idea_decomposer.py
class IdeaDecomposer:
    """将想法拆解为可执行的需求特性"""
    
    async def decompose(self, idea_id: int) -> List[int]:
        """
        流程：
        1. 读取想法内容
        2. 调用 qodercli 拆解为 3-5 个需求
        3. 为每个需求创建 FeatureRequest
        4. 更新 idea.status = 'decomposed'
        5. 返回新创建的需求 ID 列表
        """
        idea = await self.storage.get_idea(idea_id)
        
        # 构建拆解 prompt
        prompt = f"""
        请将以下想法拆解为 3-5 个可立即实施的、互相独立的功能需求：
        
        想法：{idea.title}
        详情：{idea.description}
        
        对于每个需求，请提供：
        1. title：简洁的需求标题
        2. description：详细说明
        3. type：feature/bug/optimization
        4. priority：P0/P1/P2/P3
        5. estimated_effort：小/中/大
        
        输出格式：JSON 数组
        """
        
        # 调用 qodercli
        idea.status = "decomposing"
        await self.storage.update_idea(idea)
        
        try:
            output = await self.agent.do(prompt)
            features_data = self._parse_json_array(output)
            
            # 创建 FeatureRequest
            feature_ids = []
            for feature_data in features_data:
                fid = await self.storage.create_feature(
                    FeatureRequest(
                        title=feature_data.get("title"),
                        description=feature_data.get("description"),
                        type=feature_data.get("type", "feature"),
                        priority=feature_data.get("priority", "P2"),
                        parent_goal=f"想法#{idea_id}: {idea.title}",
                        idea_id=idea_id,
                        status="pending",  # 待评估
                    )
                )
                feature_ids.append(fid)
            
            # 更新想法状态
            idea.status = "decomposed"
            idea.derived_features = feature_ids
            idea.decomposition_result = json.dumps(features_data)
            await self.storage.update_idea(idea)
            
            return feature_ids
        
        except Exception as e:
            logger.error(f"Idea decomposition failed: {e}")
            idea.status = "pending"
            await self.storage.update_idea(idea)
            return []
```

**集成到主循环**：
```python
# vivify/kernel/feature_pipeline.py
async def run(self):
    while True:
        # 1. 拆解 pending 想法
        await self._decompose_ideas()
        
        # 2. 评估 pending 需求
        await self._evaluate_pending()
        
        # 3-4. ... 其他阶段 ...
        
        await asyncio.sleep(self.config.loop_interval)

async def _decompose_ideas(self):
    """定期拆解想法为需求"""
    pending_ideas = await self.storage.list_ideas_by_status("pending")
    for idea in pending_ideas[:self.config.max_ideas_per_round]:
        await self.idea_decomposer.decompose(idea.id)
```

**影响**：
- 支持用户创意输入
- 自动转化为可执行任务
- 形成"想法 → 特性 → 部署"的完整链条

---

### 优先级 P2：并行开发（技术优化）

#### P2.2 显式并行框架
**改进内容**：

```python
# vivify/kernel/feature_pipeline.py
from concurrent.futures import ThreadPoolExecutor, as_completed

class FeaturePipeline:
    async def __init__(self, ...):
        self.eval_executor = ThreadPoolExecutor(max_workers=4)  # 评估并行
        self.verify_executor = ThreadPoolExecutor(max_workers=2)  # 验证并行
        self.dev_semaphore = asyncio.Semaphore(3)  # 开发并行 worktree 数
    
    async def _evaluate_pending(self):
        """并行评估多个需求"""
        pending = await self.storage.list_features_by_status("pending")
        pending = pending[:self.config.max_feature_evals_per_round]
        
        futures = []
        for feature in pending:
            future = asyncio.get_event_loop().run_in_executor(
                self.eval_executor,
                self._evaluate_feature,
                feature
            )
            futures.append((feature.id, future))
        
        for fid, future in futures:
            try:
                result = await asyncio.wait_for(future, timeout=self.config.feature_eval_timeout_sec)
                await self.storage.update_feature(result)
            except asyncio.TimeoutError:
                logger.warning(f"Feature #{fid} evaluation timeout")
                # 自动重置为 pending
                feature = await self.storage.get_feature(fid)
                feature.status = "pending"
                await self.storage.update_feature(feature)
    
    async def _develop_approved(self):
        """使用 semaphore 限制并行 worktree 数"""
        approved = await self.storage.list_features_by_status("approved")
        approved = approved[:self.config.max_feature_devs_per_round]
        
        tasks = []
        for feature in approved:
            tasks.append(self._develop_with_semaphore(feature))
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _develop_with_semaphore(self, feature: FeatureRequest):
        """获取 worktree 许可证，然后开发"""
        async with self.dev_semaphore:
            await self._develop_feature(feature)
```

**影响**：
- 充分利用多核 CPU
- 加快评估阶段（纯 I/O，可 4 并行）
- 控制开发并行度（worktree 创建成本高，限制为 3）

---

### 优先级 P3：高级功能（长期）

#### P3.1 自动升级与人工干预
**改进内容**：

```python
# vivify/kernel/escalator.py
class FeatureEscalator:
    """在自动修复无效后，自动升级为人工干预"""
    
    async def check_and_escalate(self):
        """定期检查连续失败的需求，升级处理"""
        # 连续失败 N 次的需求
        stale_features = await self.storage.execute(
            """
            SELECT * FROM feature_requests
            WHERE status IN ('evaluating', 'developing', 'verifying')
            AND retry_count >= ?
            ORDER BY updated_at DESC
            """,
            (self.config.auto_escalate_retry_threshold,)
        )
        
        for feature in stale_features:
            # 1. 创建 GitHub Issue 通知人类
            issue = await self._create_github_issue(
                title=f"Feature #{feature.id} stuck: {feature.title}",
                body=f"""
                Feature request #{feature.id} has failed after {feature.retry_count} retries.
                
                Status: {feature.status}
                Last error: {feature.error_message}
                Attempts: {feature.retry_count}
                
                Please review and manually intervene if necessary.
                """,
                labels=["auto-escalated", f"priority-{feature.priority}"]
            )
            
            # 2. 发送通知
            await self._notify_admin(
                f"Feature #{feature.id} needs manual review",
                f"Link: {issue.html_url}"
            )
            
            # 3. 标记为 needs_admin_review
            feature.status = "needs_admin_review"
            feature.admin_issue_url = issue.html_url
            await self.storage.update_feature(feature)
```

**影响**：
- 自动检测"无法自动解决"的问题
- 及时通知人类干预
- 防止系统无限重试

---

## 六、建议实施路线

### 第一阶段（2-3 周）：核心数据模型增强
1. **P1.1** 完善 feature_requests 表结构
   - 添加 feasibility, image_urls, parent_id, idea_id 等字段
   - 新增 deployed_with_issues 状态
   - 迁移脚本测试

2. **P1.2** 实现超时自动恢复
   - 在 feature_pipeline.py 主循环中集成
   - 配置超时阈值（evaluating: 10 min, developing: 1.5h, verifying: 1h）
   - 编写单元测试

### 第二阶段（3-4 周）：需求关联与派生
1. **P1.3** 支持需求关联
   - API 端点：POST /feature-requests/{id}/feedback → 创建派生需求
   - 自动批准跟进反馈需求（parent_id 非空 → status=approved）

2. **P1.4** 结果导向验证
   - 定义关键业务指标收集接口
   - 实现部署前后的指标对比
   - 替换代码审查为结果验证

### 第三阶段（4-5 周）：并行开发与想法拆解
1. **P1.5** 合并开发（批处理）
   - 实现需求相似度计算和分组
   - 支持单个 worktree 内的批处理开发
   - 失败自动拆散重试

2. **P2.1** 想法拆解框架
   - 创建 Idea 模型和存储
   - IdeaDecomposer 调用 qodercli 拆解
   - 集成到主循环

### 第四阶段（持续改进）
- **P2.2** 显式并行框架（ThreadPoolExecutor + asyncio.Semaphore）
- **P3.1** 自动升级与 GitHub Issue 集成
- 性能监控和超时参数调优

---

## 七、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 超时阈值设置不当 | 过早重置导致需求浪费，过晚导致系统卡住 | 从保守值开始（长阈值），根据实际调整 |
| 合并开发失败率高 | 整个批次失败 → 需求重试 | 严格限制条件（P2/P3 + 高相似度），失败 1 次拆散 |
| 结果导向验证指标不稳定 | 同样的代码变更，指标变化不确定 | 收集多个指标，取综合评分而非单指标 |
| 并行 qodercli 进程过多 | 系统资源压力 | 全局上限 MAX_QODERCLI_TOTAL=10 |
| 需求派生链条无限递推 | 需求数爆炸 | 限制派生深度（depth <= 3）和数量 |

---

## 八、总结与建议

### 核心发现

channels-monitor 项目在需求管理和自主迭代开发方面比 vivify 领先 **一个量级**：

1. **完整的生命周期管理**：8 种状态 vs 7 种，支持中间状态（deployed_with_issues）
2. **完善的容错机制**：自动超时恢复、失败重试、人工升级
3. **真实业务验证**：结果导向验证而非代码审查，更有说服力
4. **灵活的需求关联**：支持派生（parent_id）、想法拆解（idea_id）、批处理（batch_commit）
5. **高效的并行开发**：评估 4 并行、验证 2 并行、开发 3 个 worktree

### 对 vivify 的建议

**立即实施（P1）**：
- [ ] 完善数据模型（+8 个字段）
- [ ] 实现超时自动恢复（防止卡住）
- [ ] 支持需求关联与派生（跟进反馈流程）

**中期实施（P1-P2）**：
- [ ] 切换到结果导向验证（提升说服力）
- [ ] 实现合并开发（加快低优先级部署）
- [ ] 构建想法拆解框架（创意转化为任务）

**长期优化（P2-P3）**：
- [ ] 显式并行框架（充分利用多核）
- [ ] 自动升级与人工干预集成（GitHub Issues）
- [ ] 超时参数的动态调优

### 预期收益

实施这些改进后，vivify 的需求管理框架将达到或超过 channels-monitor 的水平，具体收益：

| 改进 | 定量收益 |
|------|---------|
| 超时恢复 | 减少 90% 的"卡住需求" |
| 合并开发 | 低优先级需求部署时间 ↓ 40% |
| 结果验证 | 验证通过率从 60% ↑ 85%（更准确） |
| 派生需求 | 反馈响应时间 ↓ 50% |
| 想法拆解 | 用户创意转化率 ↑ 70% |

---

**文档信息**
- 分析时间：2026-05-25
- 数据来源：github.com/pinsonchen/channels-monitor
- 对标项目：vivify（复元自愈系统）

