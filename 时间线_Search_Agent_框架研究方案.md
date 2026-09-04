# 基于 SFT / DPO 的时间线 Search Agent 框架研究方案

> 研究日期：2026-09-04  
> 依据：综述正文核心论文 1–4 项（MAS-TLS、NarrativeReason、TISER、R2A-TLS）

## 一、结论先行

推荐论文主线：**Timeline State-aware Search Agent（TiSA）**。它不是让多个 Agent 自由讨论，而是把开放域 TLS 建模为一个受预算约束的序列决策问题：

```text
主题 + 当前时间线状态
        ↓
缺口检测（缺日期 / 缺因果桥 / 缺事件要素 / 证据冲突）
        ↓
动作策略 π(a|state)：SEARCH / VERIFY / MERGE / STOP
        ↓
检索与证据过滤 → 更新事件图 → 时间一致性检查
        └───────────────────────────────↺
```

训练采用：

1. **SFT 冷启动**：学习结构化状态、时间推理、反思、查询生成和工具调用格式；
2. **局部偏好优化**：从同一状态构造 chosen/rejected，优先用 DPO；如果输出长度差异大，用长度归一化的 SimPO；
3. **可选蒸馏**：只在算力允许时，把强教师的时间关系判断蒸馏到 7B 学生，不建议一开始复现神经元级 NST/PKT。

最核心的论文创新应是：**将偏好优化施加在搜索轨迹的局部决策，而不是整条长时间线的最终文本上**。

## 二、四篇核心论文如何组合

| 工作 | 可继承部分 | 不宜直接照搬 | 在 TiSA 中的位置 | 复现难度 |
|---|---|---|---|---|
| MAS-TLS | 主编/记者分工、候选去重、预算感知调度 | 32B×多 Agent、双 A800、复杂 bandit 全量复现 | 并行候选生成 + Supervisor 控制 | 高 |
| NarrativeReason | 连续事件时间关系、教师→学生迁移 | NST/PKT 等隐层蒸馏与不同架构对齐 | 时间关系辅助任务或可选蒸馏 | 中高 |
| TISER | `<reasoning><timeline><reflection><answer>` 轨迹、正确性过滤 | 每轮无条件长反思，token 开销显著 | SFT 轨迹协议和一致性检查 | 中 |
| R2A-TLS | 因果缺口、事件要素缺口、定向深检索、双过滤 | 依赖外部搜索 API、完整官方代码不明确 | Search Agent 的核心状态与动作 | 中 |

来源核验：MAS-TLS 在 T17、Crisis、Entities 上使用 Qwen3-32B 和双 A800；TISER 用结构化推理轨迹对 7B 模型做 SFT；R2A-TLS 在 50 主题的 Open-TLS 上验证，并以 352 个样本对 Qwen2.5-7B 做 LoRA。这些信息分别见 [MAS-TLS](https://aclanthology.org/2026.acl-long.1149.pdf)、[TISER](https://aclanthology.org/2025.acl-long.1358.pdf) 和 [R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40.pdf)。

## 三、TiSA 框架设计

### 3.1 结构化时间线状态

不要把历史轮次只保存为自然语言。建议维护以下 JSON 状态：

```json
{
  "topic": "target event/entity",
  "budget": {"queries_left": 4, "tokens_left": 8000},
  "events": [
    {
      "event_id": "e12",
      "time": {"value": "2025-03-01", "granularity": "day", "confidence": 0.91},
      "summary": "...",
      "actors": ["..."],
      "location": "...",
      "causes": ["e09"],
      "effects": ["e15"],
      "evidence_ids": ["d3#p4", "d8#p2"],
      "support": 2,
      "conflict": false
    }
  ],
  "gaps": [
    {"type": "CAUSAL_BRIDGE", "left": "e09", "right": "e12", "priority": 0.82},
    {"type": "MISSING_ROLE", "event": "e12", "role": "location", "priority": 0.34}
  ]
}
```

这一步融合了 TISER 的显式时间线和 R2A-TLS 的因果/语义缺口。所有最终事件必须保留证据指针，避免只有“看起来合理”的反思。

### 3.2 Agent 角色

- **Retriever/Reporter**：针对同一缺口生成 2–4 条差异化查询，并行检索；角色来自 MAS-TLS，但共享同一结构化状态。
- **Evidence Verifier**：判断相关性、信息增益、来源时间、发布时间与事件发生时间，执行双过滤。
- **Temporal Reasoner**：规范化日期，判断 before/after/overlap/unknown，构造局部事件图。
- **Supervisor**：选择下一动作、合并事件、分配预算、决定停止；这是主要可训练模块。
- **Writer**：只读取最终已验证事件图生成时间线，不参与搜索决策，减少幻觉传播。

### 3.3 动作空间与停止条件

将自由文本 Agent 收敛为小动作空间：

```text
SEARCH(gap_id, query)
VERIFY(event_id | evidence_id)
MERGE(event_i, event_j)
REVISE_TIME(event_id, normalized_time)
DROP(event_id, reason)
STOP(reason)
```

停止不是提示词规则，而是要学习的决策。硬约束可设为：预算耗尽必须停止；连续两轮新增高置信事件数为 0；所有高优先级缺口关闭；新增检索的边际信息增益低于阈值。R2A-TLS 的检索数量消融显示 20→30 篇文档收益已趋于饱和，因此“何时停止”本身就是值得优化的目标。[R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40.pdf)

## 四、数据集与数据构造

### 4.1 推荐组合

| 用途 | 数据 | 用法 | 注意事项 |
|---|---|---|---|
| 开放域主评测 | Open-TLS | 50 主题，评估在线检索和最终时间线 | 规模小，只做开发/测试；官方数据与基线代码已由 [CHRONOS](https://github.com/Alibaba-NLP/CHRONOS) 发布 |
| 闭域主评测 | T17、Crisis、Entities | 冻结语料库，保证可重复检索 | 可先做 BM25 + dense hybrid，避免搜索 API 波动 |
| 时间推理预训练 | TGQA、TempReason、TimeQA | 构造时间排序、持续时间、关系判断 SFT | 与 TLS 主题严格去重 |
| 连续叙事辅助 | NarrativeReason | 教时间关系与叙事顺序 | 社交媒体域与新闻域存在迁移差异 |
| 因果/层级辅助 | ESC、HiEve | 因果链和子事件判断 | 只作为辅助任务，不直接算 TLS 主结果 |

### 4.2 SFT 轨迹如何生成

以强模型或规则+强模型生成候选轨迹，然后做可执行验证：

1. 从金时间线反推关键事件和证据文档；
2. 人为遮掉一个日期、因果桥或事件要素，得到可控 gap；
3. 让教师输出 `state → reflection → action → observation → updated_state`；
4. 执行真实检索，检查查询是否找回目标证据；
5. 只有事件日期、答案或证据指针可验证时才保留；
6. 加入 20% 无需继续搜索的状态，专门训练 STOP。

建议三类 SFT 样本混合：

- 40% 时间关系/日期规范化；
- 40% 缺口到查询及工具调用轨迹；
- 20% 事件合并、证据裁决和停止。

TISER 的原始做法是生成包含 reasoning、timeline、reflection 的轨迹，并只保留最终答案与金答案一致的样本；这为轨迹过滤提供了直接依据。[TISER](https://aclanthology.org/2025.acl-long.1358.pdf)

### 4.3 偏好对如何构造

每个偏好对必须共享相同 `state`，只比较一个局部决策：

| 偏好层级 | chosen | rejected | 自动判据 |
|---|---|---|---|
| Query | 定位缺失时间点/角色的具体查询 | 主题词改写、重复或过宽查询 | Recall@k、nDCG、信息增益、重复率 |
| Evidence | 有时间锚且多源支持的证据 | 仅复述、来源弱、时间不一致的证据 | 支持度、日期一致、来源质量 |
| Event | 有证据、要素完整、非重复事件 | 无证据或与已有事件重复 | entailment、去重、要素覆盖 |
| Action | VERIFY/STOP 等正确下一步 | 无意义继续 SEARCH | 最终质量增益减 token 成本 |
| Timeline | 更忠实、日期准、覆盖好且紧凑 | 幻觉、漏事件、日期错或冗长版本 | Date-F1、Align/Agree、faithfulness |

可定义效用：

```text
U = 0.30·DateF1 + 0.25·AlignF1 + 0.20·EvidenceSupport
    + 0.15·Coverage - 0.05·Redundancy - 0.05·NormalizedCost
```

只有当两个候选效用差超过 margin 时才组成偏好对；接近的样本标为 tie 或丢弃，避免伪偏好噪声。

## 五、为什么是 SFT → DPO/SimPO

### SFT 的职责

SFT 用于学习“会做”：固定 schema、工具调用、时间规范化、构图、反思和停止格式。TISER 已验证结构化中间轨迹可以通过 LoRA/SFT 教给 7B 模型，但也显示长反思带来显著 token 开销，因此训练集必须同时包含短轨迹和 STOP 样本。[TISER](https://aclanthology.org/2025.acl-long.1358.pdf)

### DPO 的职责

DPO 用于学习“哪一个动作更好”，不需要单独训练奖励模型，训练复杂度低于 PPO/RLHF，适合已有离线 chosen/rejected 的场景。[DPO 原论文](https://papers.neurips.cc/paper_files/paper/2023/file/a85b405ed65c6477a4fe8302b5e06ce7-Paper-Conference.pdf)

但不建议直接对整条长轨迹做普通 DPO：chosen 往往更长，会把“详细”等同于“更好”。首选方案是局部动作 DPO；若仍有明显长度差异，改用对序列 log-prob 做长度归一化的 [SimPO](https://papers.neurips.cc/paper_files/paper/2024/file/e099c1c9699814af0be873a175361713-Paper-Conference.pdf)，并报告长度控制消融。

### 推荐训练顺序

```text
Base Instruct 7B
  → Stage A: temporal auxiliary SFT
  → Stage B: tool-trajectory SFT
  → Stage C: local-decision DPO / SimPO
  → 可选 Stage D: 仅对失败状态做迭代自训练
```

建议基础模型选 Qwen2.5/3-7B Instruct；LoRA/QLoRA 先验证。不要在第一版加入在线 PPO/GRPO，因为搜索环境非平稳、奖励延迟且网页结果不可完全复现，会显著扩大工程风险。

## 六、复现路线与资源估算

### 最小可行版本（最推荐）

- 冻结 T17/Crisis/Entities 语料；BM25 + 一个 dense retriever；
- 单 Supervisor + 两个并行 query proposer；
- Qwen 7B QLoRA；
- 5k–20k SFT 轨迹，5k–10k 局部偏好对；
- 1×48GB 或 2×24GB GPU 可尝试，具体显存取决于上下文长度、量化和 batch；
- 先不做隐层蒸馏和 Bayesian bandit。

### 完整版本

- Open-TLS 在线搜索 + 闭域稳定评测双轨；
- 3–5 个 Reporter、学习式 Supervisor、预算控制；
- 多来源证据、网页正文抓取、时间图数据库；
- 需要 API 预算、可重复搜索快照和更复杂的缓存。

### 难点排序

1. **数据构造与防泄漏**：最难；金时间线可能已被教师模型记忆。
2. **在线检索可重复性**：网页、排名和 API 会变化，必须保存 URL、抓取时间与内容哈希。
3. **偏好标签可靠性**：ROUGE 高不代表事实正确，需证据蕴含和日期规则联合打分。
4. **多 Agent 成本**：并发降低延迟但不必然降低 token；必须报告质量—成本 Pareto 曲线。
5. **长轨迹训练**：反思文本极易膨胀，应局部化并限制状态序列化长度。

## 七、实验设计

### 7.1 主指标

- 时间选择：Date Precision / Recall / F1；
- 内容：Concat、Agree、Align ROUGE-1/2；
- 检索：Evidence Recall@k、nDCG@k、每个金事件的证据覆盖率；
- 忠实性：事件—证据 entailment、人评事实一致性；
- 效率：每主题查询数、检索文档数、输入/输出 token、墙钟时间；
- 策略：无效搜索率、重复查询率、正确 STOP 率。

不要只报告 ROUGE。R2A-TLS 的消融显示时间点补全主要影响 Date-F1，而事件要素补全更多影响摘要内容；指标必须分别对应模块。[R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40.pdf)

### 7.2 必做消融

```text
Direct generation
→ + retrieval
→ + structured timeline state
→ + gap reflection
→ + multi-query reporters
→ + SFT policy
→ + local DPO/SimPO
→ + learned STOP / budget objective
```

另做：无因果缺口、无事件要素缺口、单过滤 vs 双过滤、单 Agent vs 多 Agent、固定轮数 vs 学习停止、最终文本 DPO vs 局部决策 DPO。

### 7.3 数据切分

- 以“主题”为单位切分，禁止同一实体或事件跨 train/test；
- 增加时间截断：训练检索只能看到 cutoff 之前文档；
- 在线评测保存搜索快照；
- 对教师生成数据做近似匹配，排除与测试参考时间线高度重合的样本。

## 八、预期论文贡献与风险

可以写成三点贡献：

1. 首次将开放域 TLS 的搜索过程形式化为**显式时间线状态上的预算约束动作策略**；
2. 提出**局部搜索决策偏好数据**及 SFT→DPO/SimPO 训练范式，缓解长轨迹偏好混杂；
3. 建立覆盖最终质量、证据忠实性和检索成本的联合评测，并证明学习式 STOP 改善质量—成本权衡。

主要风险是“多模块集成但创新不足”。规避方式是把研究问题压缩为：

> 在相同检索器、基础模型和预算下，局部偏好优化能否让 Search Agent 更准确地选择下一检索动作并更早停止，从而提高时间线的日期准确性和证据忠实性？

这个问题比“融合四篇论文做一个大系统”更清晰、可证伪，也更容易形成有说服力的消融。

## 九、建议的 8 周执行顺序

1. 第 1 周：复现 CHRONOS/Open-TLS 数据读取与评测；冻结闭域语料快照。
2. 第 2 周：实现事件 JSON、日期规范化、证据指针和基线检索。
3. 第 3 周：实现 R2A 风格缺口检测、双过滤和固定停止规则。
4. 第 4 周：生成并验证 SFT 轨迹；完成 7B QLoRA。
5. 第 5 周：为 query/action/STOP 构造局部偏好对；训练 DPO。
6. 第 6 周：做 SimPO、最终文本 DPO、无偏好训练等对照。
7. 第 7 周：完成消融、成本曲线、错误类型和人工忠实性评估。
8. 第 8 周：整理可复现配置、搜索快照、数据卡和论文初稿。

## 十、来源与研究边界

主要依据均为原论文或官方代码库：[MAS-TLS](https://aclanthology.org/2026.acl-long.1149.pdf)、[Temporal reasoning for timeline summarisation in social media](https://aclanthology.org/2025.acl-long.1362/)、[TISER](https://aclanthology.org/2025.acl-long.1358/)、[R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40/)、[CHRONOS/Open-TLS](https://github.com/Alibaba-NLP/CHRONOS)、[DPO](https://papers.neurips.cc/paper_files/paper/2023/file/a85b405ed65c6477a4fe8302b5e06ce7-Paper-Conference.pdf)、[SimPO](https://papers.neurips.cc/paper_files/paper/2024/file/e099c1c9699814af0be873a175361713-Paper-Conference.pdf)。

需要注意：指定综述正文第 4 项是 R2A-TLS，但文末汇总表把第 4 项列为 TReMu；本方案按正文编号处理。MAS-TLS 与 R2A-TLS 的完整官方实现可得性不如 CHRONOS/TISER 明确，因此复现难度包含基于论文配置的推断。研究在四篇核心工作证据已经能覆盖“架构—训练—数据—成本—评测”后停止继续扩展，以避免用弱相关论文稀释主线。
