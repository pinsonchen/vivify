# Vivify 活体系统演进路线图

> 从"死板工具"到"活体系统"——让 Vivify 成为一个戴着镣铐跳舞的学徒：拥有无限的战术创造力，但受到绝对的战略性约束。

---

## 核心哲学

传统软件系统像精密但脆弱的机械钟表，出厂即定型、坏了靠人修。引入 AI "生长因子"后，Vivify 应蜕变为具备新陈代谢能力的"变形虫"——越用越聪明，能自我修复、自我进化、自我约束。

---

## 生命特征的三大维度

### 免疫系统（自愈）

通过实时感知与隔离病灶，结合动态修复或降级策略，实现无需人工干预的自我愈合。

### 新陈代谢（生长）

数据流在循环中不断进化，系统能够沉淀经验、繁衍知识图谱，做到"越用越聪明"。

### 细胞分裂（弹性演化）

面对未知需求，AI Agent 能自主生成并销毁子模块，并根据负载自适应调配算力资源。

---

## 演进方向

### 1. 免疫记忆——从"修了就忘"到"抗体沉淀"

**现状**：Probe → Fixer → Agent 三级修复 + Harness PEV 验证 + Doom-loop 检测已形成免疫骨架。

**演进目标**：

- **修复模式固化**：Agent 成功修复后，自动执行"反思" pass 提取修复策略，沉淀为 `.vivify/capsules/<probe_id>_<hash>.json` 格式的技能胶囊。下次同类问题直接走 fast-path，无需再次调用 AI。

- **免疫分级响应**：对重复出现 3 次以上的同类问题，自动提升优先级并触发"紧急隔离"（跳过开发，直接 revert 引入问题的 PR）。将 `DoomLoopDetector.get_escape_strategy()` 与 `AutoReverter` 打通。

- **抗体库**：`FailureTracker` 演进为双向记录器——既记录失败模式（避免重蹈覆辙），也记录成功模式（加速未来修复）。

**关键数据结构**：

```python
@dataclass
class SkillCapsule:
    trigger_pattern: str      # 触发条件（probe_id + issue 特征）
    fix_strategy: str         # 修复策略摘要
    success_count: int        # 被复用成功次数
    last_used: datetime
    prompt_template: str      # 可直接注入 agent prompt 的模板
    source_action_id: str     # 来源 ActionLog ID
```

---

### 2. 新陈代谢——知识图谱的"生长"与"遗忘"

**现状**：`KnowledgeMaintainer` 做增量更新，但只有生长没有代谢。

**演进目标**：

- **主动遗忘机制**：新增 `knowledge/gc.py`，根据 git log 检测已删除模块/文件，自动清理对应的知识节点。每 N 轮触发一次垃圾回收。

- **经验营养分级**：为 `ActionLog` 增加 `utility_score` 字段，基于以下维度评分：
  - 修复是否被 revert（负分）
  - PR 合并后是否引入新问题（负分）
  - 修复模式是否被复用过（正分）
  - 修复效率（耗时 vs 同类平均）

  低分经验定期淘汰，高分经验优先注入 Agent 上下文。

- **知识压缩**：当知识图谱超过阈值大小时，触发"总结压缩"——将多个细粒度节点合并为高层抽象节点，保持系统大脑的清醒。

---

### 3. 弹性演化——动态 Agent 与算力调配

**现状**：`SlotManager` 管理并发进程数，所有任务走同一个 `QoderCliAgent`，通过 `agent_for_category` 区分 Agent 类型。

**演进目标**：

- **动态 Agent 工厂**：新增 `agents/agent_factory.py`，当某类探针持续触发且无对应 Fixer 时，自动生成专门的"微 Agent"——包含针对该问题的 system prompt + 专用 tools，注册为新的 Agent Profile。

- **算力弹性调配**：根据 pending issues/features 队列深度，动态调整 `max_concurrent_processes`：
  - 空闲时缩到 1，节省资源
  - 积压时自动扩到配置上限
  - 高优先级任务可抢占低优先级任务的 slot

- **Agent 表现追踪**：为每个 Agent 配置记录成功率，表现好的获得更多资源（max_turns ↑），表现差的被降级或回收。

---

### 4. 宪法约束 + 物理沙箱——安全底座

**现状**：`self_grow_guard.py` 实现对 vivify 自身修改的分级管控，`risk_scorer.py` 做启发式风险评估。

**不可逾越的底层基因法则**：

#### 4.1 生存底线

硬编码非破坏性原则和数据主权隔离，并预留物理层级终止权。

- **Worktree 级物理隔离**：在 `WorktreeManager.create()` 时设置 git pre-commit hook，硬性禁止删除宪法级文件（`vivify/kernel/`、`vivify/interfaces/`、`vivify/config/schema.py`）。
- **敏感文件不可见**：通过 sparse-checkout 让 Agent 在 worktree 中根本看不到 `.env`、credentials 等文件，而非仅仅"不 commit"。

#### 4.2 代谢限制

从物理层面限制系统的算力和 API 调用配额，防止其陷入无限循环或疯狂扩张。

- **全局令牌桶（TokenBudget）**：基于时间窗口的令牌桶，在 `QoderCliAgent.heal()` 入口处检查，超额直接拒绝而非排队。
- **每日/每轮预算硬限**：不可被任何 Agent 行为突破，只能由人类手动提升。

#### 4.3 价值对齐

坚持最小权限和诚实原则，杜绝 AI 幻觉；所有重大生长必须自带无损回滚路径。

- **所有自修改 PR 必须可回滚**：在 PR body 中自动生成 revert 命令。
- **幻觉检测**：Agent 声称"修复成功"但 PEV 不通过时，记录为 `hallucination` 事件并降低该 Agent 配置的信任分。

---

### 5. 技能胶囊——"反思-提炼"闭环

**核心机制**：构建"反思-提炼"闭环，将成功的执行路径固化为可复用的"技能胶囊"，实现指数级成长。

**实现路径**：

1. **触发时机**：每次 `_try_agent_fix` 返回 True 后。
2. **反思 Pass**：调用 Agent（轻量模式，max_turns=5）总结本次修复的通用策略。
3. **结构化存储**：输出为 SkillCapsule JSON，存入 `.vivify/capsules/`。
4. **复用注入**：在 `builders.build_fix_issue()` 中查询匹配的 capsules，作为 `known_fix_pattern` 注入 prompt。
5. **淘汰机制**：长期未复用（30 天）或复用失败率高的 capsule 自动归档。

**预期效果**：随着运行时间增长，fast-path 覆盖率持续提升，AI 调用频率逐步下降。

---

### 6. 多模态奖励模型——内部裁判

**现状**：`quality_check.py` 做静态质量门禁，`harness/sensors.py` 做 PEV 验证。缺少全局"这次修复是否真的好"的评判。

**演进目标**：

新增 `reward_signals` 表，记录多维奖励信号：

| 维度 | 数据来源 | 权重 |
|------|----------|------|
| correctness | PEV 验证是否通过 | 0.4 |
| efficiency | 修复耗时 vs 同类平均 | 0.2 |
| stability | 合并后 7 天内是否引入新 issue | 0.3 |
| elegance | diff 行数 vs 问题复杂度比 | 0.1 |

奖励信号的消费端：
- 反馈到 `AgentCostModel` 动态调整
- 影响 SkillCapsule 的 utility_score
- 驱动 Agent Profile 的升级/降级

---

### 7. 分层记忆架构（L0-L4）

**现状**：仅有 SQLite 持久化 + 知识图谱 JSON，无明确记忆分层。

**目标架构**：

| 层级 | 内容 | TTL | 实现位置 |
|------|------|-----|----------|
| L0 Working | 当前轮次的 issue/context | 单轮 | `run_once()` 内局部变量 |
| L1 Short-term | 最近 5 轮修复尝试 | ~1 小时 | `FailureTracker` + `_rca_contexts` |
| L2 Episodic | 过去 7 天的成功修复模式 | 7 天 | 新增 `memory/episodic.py` |
| L3 Semantic | 知识图谱 + Capsules | 永久(可 GC) | `knowledge/` + `capsules/` |

**关键缺口——L2 Episodic Memory**：

当前 Agent 每次修复"从零开始"，不知道 3 天前有过类似修复。新增 `memory/episodic.py` 基于 ActionLog 查询最近相似修复，在 `builders.build_fix_issue()` 中注入"最近相似修复"的上下文。

---

## 实施优先级

按 ROI（投入产出比）排序：

| 优先级 | 方向 | 预期效果 | 工作量 |
|--------|------|----------|--------|
| P0 | 技能胶囊系统 | 修复成功 → 提炼 → 复用，fast-path 自动生长 | 中 |
| P0 | API 令牌桶硬限 | 防止失控循环烧钱，安全关键 | 小 |
| P1 | 知识遗忘/GC | 防止知识图谱膨胀降低 Agent 效果 | 小 |
| P1 | L2 Episodic Memory | 显著提升修复命中率 | 中 |
| P2 | 多模态奖励信号 | 形成正向反馈循环 | 中 |
| P2 | Worktree 物理隔离 | 补全安全底座 | 小 |
| P3 | 动态 Agent 工厂 | 长期价值，需较大架构调整 | 大 |
| P3 | 算力弹性调配 | 资源利用率优化 | 中 |

---

## 设计原则

1. **最小权限**：Agent 只能访问完成任务所必需的文件和工具。
2. **可观测性**：所有 Agent 行为必须有 trace，所有决策必须可审计。
3. **渐进式自治**：系统自治程度随信任分积累逐步提升，而非一步到位。
4. **无损回滚**：任何 Agent 产生的变更都必须可在 60 秒内完全回滚。
5. **代谢平衡**：生长速度不超过消化速度——新知识的注入速率 ≤ 旧知识的验证速率。

---

## 结语

Vivify 的五层架构（Detection → Fast-path → Intelligence → Development → Verification）已经很好地对应了活体系统的骨架。下一阶段的核心突破在于：

- **闭环学习**：让成功经验自动固化为能力（技能胶囊）
- **代谢平衡**：让系统既能生长也能遗忘（知识 GC + 令牌桶）
- **安全底座**：让约束从"软件层"下沉到"物理层"（worktree 隔离 + 硬限）

不要重写你的项目，让它自己长出来——但要确保它长在正确的方向上。
