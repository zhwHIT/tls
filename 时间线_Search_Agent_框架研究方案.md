# 基于 SFT / DPO 的时间线 Search Agent 框架研究方案

> 最新结果（2026-09-16）：v9.5 续跑已结束，7/9 主题完成，Haiti、Iraq 达到请求上限；完成主题中 Egypt、H1N1、MJ 日期召回达到 70%，全主题聚合尚不可用。7 个完成主题第二阶段均为预算停止，自主停止和事件语义正确性仍未验收。详见 [54_全T17续跑结果与GitHub同步](进度/54_全T17续跑结果与GitHub同步.md)。

> 最新恢复（2026-09-16，v9.5）：v9.4 全 T17 的 9 个主题均未完整成功。已修复 Gap 输出协议、Goldsmith 误判和检查点序列化问题，并启动状态续跑，承接 775 条事件和原累计 2876 次请求，每主题最多新增 300 次，阶段预算不重置。330 项完整测试通过；这是开发状态续跑，最终评测待验收。详见 [53_中断状态恢复与Gap协议修复](进度/53_中断状态恢复与Gap协议修复.md)。以下更新按时间保留。

> 最新执行（2026-09-16，v9.4）：按用户授权将单条事实摘要上限提高至 320 字符、建议 240 字符，仍最多 8 条、默认每 3 批更新，输入预检 3800/API 硬上限 4096 tokens 不变。324 项测试通过，已启动全 T17 串行重跑，结果尚未验收。详见 [52_摘要长度放宽与全T17重跑](进度/52_摘要长度放宽与全T17重跑.md)。

> 当前修复（2026-09-16，v9.3）：摘要和查询错误按具体字段反馈，重试仍失败时记录保守恢复；重复查询的执行器交接不作为自主 STOP，精确重复 APPEND 不再中断其他有效候选。318 项离线测试、9 项历史失败回放和 9 项 API 组件复测通过，完整 T17 覆盖率仍未达标。详见 [51_v93三类中断修复与回归验证](进度/51_v93三类中断修复与回归验证.md)。

> 当前执行更新（2026-09-15，v9.2）：第二阶段 STOP 仅暂缓当前 gap，其他 gap 继续调度；支撑事件变化后重新审核已关闭 gap。修复失败响应计费统计，启动 T17 全部 9 个主题的授权 API 评测。因并行加载内存不足，改为串行执行。当前运行未结束，覆盖率和完整流程仍待验收。详见 [50_全T17授权评测与框架修复](进度/50_全T17授权评测与框架修复.md)。

> 授权实测更新（2026-09-15，v9.1）：MJ 3 批 6 条查询、1 次延迟摘要已经实际执行；修正 JSON null 和 gap thought 长度提示后，3 项 API 定点复测通过。24/24 次授权请求已用完，全部输入最大 2281 tokens，学生输出最大 107。原流程在 gap 初始化格式校验失败后停止，完整第二阶段仍待验证。详见 [49_MJ批量查询授权实测与Prompt修复](进度/49_MJ批量查询授权实测与Prompt修复.md)。

> 最新实现（2026-09-15，v9）：学生只学习根据事实摘要、事件增量和 gap 输出批量 SEARCH 或 STOP；MEMORY_UPDATE、GAP_MEMORY 交由固定 API。摘要默认按 3 个搜索批次或上下文压力更新，期间传递 APPEND/UPDATE/DELETE。新入口取消最低事件数、阶段数和轮数的停止门槛；加入 DeepSeek 官方 tokenizer 预检与服务端 4096 输入 token 核验、学生 512 输出限制。详细实现、prompt 审核及当前 API 外发审批状态见 [48_批量查询与延迟摘要框架_审核及API预检](进度/48_批量查询与延迟摘要框架_审核及API预检.md)。下文较早的多动作学生与逐轮摘要方案保留作历史记录，以本条和第 48 份进度为当前执行口径。

> 完整试跑准备（2026-09-14）：164 项离线测试和真实本地混合检索预检通过；完整入口新增 dry-run、显式 API 开关及跨重启请求账本。本轮没有新模型调用。下一步按独立确认的 Egypt 片段外发范围运行完整 SEARCH/Memory/Gap/STOP 轨迹，不能由历史 88% 日期精度推导出充分覆盖；实际烟测 Gold 日期召回为 18.03%。详见 [36_v6完整两阶段预检与调用范围确认](进度/36_v6完整两阶段预检与调用范围确认.md)。

> 授权实测补充（2026-09-14）：v6 固定 8 片段经 DeepSeek 提取与合并得到 25 个事件，Gold 日期命中 22/122（18.03%），累计 17 次 HTTP 请求。修复连续前文日期引文、日期区间提示和片段 ID 引用，并增加烟测跨重启计数与已提交批次恢复。152 项测试及来源审计通过；完整检索、Memory、gap、自主停止与语义覆盖尚未验收，不能据此声称已覆盖大部分事件。详见 [35_v6_Egypt授权小规模实测与验收](进度/35_v6_Egypt授权小规模实测与验收.md)。

> coverage-v6 实现补充（2026-09-11）：参考 CHRONOS 闭域约 500 词片段，接入文档召回后的连续片段选择、未读队列、分批/分页事件提取、合并后 memory 与最终全局选材；Gold 仅用于事后评测。146 项离线测试通过，真实大部分事件/日期覆盖尚未验收。详见 [34_v6覆盖导向框架与论文检索配置落地](进度/34_v6覆盖导向框架与论文检索配置落地.md)。

> 2026-09-11 修复补充：正式日级事件要求可回指原文的日期证据，年月级线索留在探索 memory；每轮 merge 后回写真实新增事件/证据增益，抑制相近无增益查询并允许明确暂缓未证实方向；gap 按尝试次数公平调度，运行上限 STOP 不作 SFT 正例。137 项离线测试通过，尚未获得新的 API 完整轨迹或覆盖分数。详见 [33_日期证据与检索停止及Gap调度修复](进度/33_日期证据与检索停止及Gap调度修复.md)。

> 2026-09-11 实现补充：第一阶段采用 SEARCH→MEMORY_UPDATE→VERIFY→MERGE；根据返回文档提取带出处的暂定事件/时间和粗阶段，再以通用 few-shot、冻结 keywords 及动态关键词发散早期/中间/后续检索。不人为写入 Egypt 专用政治危机任务描述。探索 memory 跨阶段保留；第一阶段至少 6 轮后开放自主停止、16 轮安全上限不作为 STOP 训练正例。实现、边界及真实小规模检查见 [31_第一阶段文档驱动Memory与自主发散检索](进度/31_第一阶段文档驱动Memory与自主发散检索.md)。

> 首版日期：2026-09-04；本次更新：2026-09-07
> 最新实证补充（2026-09-11）：新版 Egypt 完整试跑得到 94 个事件，Date F1 从 0.1727 升至 0.3226，但第一阶段上限停止、7 个 gap 未解决、文本匹配代理仅匹配 6/201 个 Gold 事件。下一步优先解决日期精度、聚合页面时间元数据可信度、检索意图重复与 gap 冷却调度，再做等轮数消融和扩量；不把发布日期元数据上界作为硬停止条件。详见 [32_Egypt文档Memory完整试跑与诊断](进度/32_Egypt文档Memory完整试跑与诊断.md)。
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

最核心的论文创新应是：**将偏好优化施加在搜索轨迹的局部决策，而不是整条长时间线的最终文本上**。考虑到本项目是毕业设计，方案不追求把问题压缩成单一算法点，而采用“可实现、可训练、可消融”的组合创新：两阶段时间线检索、阶段自适应结构化 Memory、Gold 驱动的局部偏好数据，以及冻结通用模型负责 VERIFY/MERGE 的模块化执行链。

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

### 3.4 本次更新：两阶段 Memory Search Agent

本毕业设计采用两个连续阶段。两阶段共同维护最终时间线状态，但检索 Memory 的目标和字段不同。

```text
Phase 1：SKELETON_EXPLORATION（骨架探索）
  空时间线 + 初始 Memory
  → 粗粒度覆盖思考
  → SEARCH(query)
  → VERIFY → MERGE
  → 更新时间覆盖、锚点事件、历史查询及边际增益
  → 继续 SEARCH 或 SWITCH_PHASE

Phase 2：GAP_REFINEMENT（缺口精修）
  已有时间线 + 阶段二 Memory
  → 分析因果断点、缺失要素、证据冲突和未覆盖区间
  → SEARCH(target_gap_query)
  → VERIFY → MERGE
  → 标记 gap 为 OPEN / PARTIAL / CLOSED
  → 继续 SEARCH 或 STOP
```

第一阶段不是“完全不思考”，而是**不执行昂贵的逐事件因果反思**。每轮只输出可监督的粗粒度检索意图，例如目标时期、目标事件类型和下一查询。第二阶段才启用因果链、事件要素和证据冲突诊断。这样既保留“检索前先思考”，又避免冗长、难评测的自由文本反思。

R2A-TLS 已经采用 initial retrieval→reflection→deep retrieval，因此“两阶段”本身属于可靠的研究基础，不单独声称全新。本文新增并重点验证的是：阶段不同则 Memory schema 不同、阶段切换可以学习、查询和停止由 Gold 驱动的局部偏好训练。这个定位对毕业设计有足够创新性，同时工程风险可控。[R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40/)

### 3.5 双 Memory 的最小结构

Memory 不应等于完整聊天记录，也不应无限追加所有文档。它是从时间线状态和检索历史压缩出的、可校验的控制状态。

```json
{
  phase: SKELETON_EXPLORATION,
  coverage_memory: {
    time_bounds: [start, end],
    covered_periods: [],
    anchor_event_ids: [],
    uncovered_periods: [],
    covered_facets: [],
    query_digest: [
      {query: ..., target: ..., new_event_count: 3, gain: 0.42}
    ],
    low_gain_streak: 0
  }
}
```

```json
{
  phase: GAP_REFINEMENT,
  gap_memory: {
    open_gaps: [
      {gap_id: g12, type: CAUSAL_BRIDGE, priority: 0.86, status: OPEN}
    ],
    closed_gaps: [],
    missing_elements: [],
    conflicts: [],
    attempted_queries: [],
    evidence_support: {},
    low_gain_streak: 0
  }
}
```

历史 query 只保存规范化摘要、目标和结果增益；原始检索文档保存在 evidence store，通过 ID 引用。这能控制上下文长度，也让“Memory 是否忠实、是否遗忘、是否错误关闭缺口”成为可评测对象。Amber 已证明“空 Memory→迭代更新→基于充分性继续检索或停止”的架构可行；本方案把它改造成 TLS 专用的时间覆盖 Memory 与因果—要素缺口 Memory。[Amber](https://aclanthology.org/2025.findings-acl.418/)

### 3.6 只训练控制器，保留完整四动作轨迹

运行日志仍完整记录 `SEARCH / VERIFY / MERGE / STOP`，但第一版不要求同一个 7B 模型把所有动作都学好：

| 模块 | 第一版实现 | 核心 DPO 对象 | 原因 |
|---|---|---:|---|
| Controller | 专用可训练模型；输出 Memory update、`SEARCH(query)`、`SWITCH_PHASE` 或 `STOP` | 是 | 直接决定覆盖率、检索成本和过早停止 |
| Retriever | 冻结 BM25/dense 检索器 | 否 | 保证回放稳定，隔离策略变量 |
| VERIFY | 冻结通用模型批量抽取候选 + 程序校验证据 ID、日期和支持关系 | 暂不做 | 适合通用模型，但不能当无误差黑盒 |
| MERGE | 通用模型选择 APPEND/UPDATE/DROP；UPDATE 单独融合；程序校验 ID、日期、证据并集 | 暂不做 | 受约束的去重与融合适合模块化复用 |
| Writer | 冻结通用模型或模板 | 否 | 只消费最终已验证事件 |

因此，**完整系统是多动作 Agent，核心训练任务是检索控制**。VERIFY/MERGE 的好坏样本继续保存，可用于错误分析、少量 SFT 或后续扩展；只有当消融证明它们是主要瓶颈时，再增加对应 DPO。

### 3.7 控制器的统一输出协议

每次搜索前要求模型读取当前时间线和当前 Memory，先产生短结构化判断，再决定动作。输出字段固定为 `thought.search_need`、`thought.target`、`thought.reason_code`、`memory_update`、`action` 和可选 `query`。

阶段一允许 `SEARCH` 或 `SWITCH_PHASE`；阶段二允许 `SEARCH` 或 `STOP`。预算耗尽、JSON 非法、引用不存在证据等仍由程序硬约束，不交给偏好模型学习。ReAct 证明交错 reasoning/action/observation 有助于维护行动计划；这里把自由思考限制为可验证字段，而非保存不可控的长链式思维。[ReAct](https://openreview.net/pdf?id=WE_vluYUL-X)

### 3.8 阶段切换与停止标准

- `SWITCH_PHASE`：骨架已有起点、主要发展区间和终点候选；时间覆盖与事件类型覆盖达到阈值；最近两轮粗搜边际增益低，但仍可从已有时间线诊断细粒度 gap。
- `STOP`：所有高优先级 gap 已关闭，最近两轮没有新增 Gold-independent 高置信事件/证据，或继续检索的预期增益低于成本。
- 硬停止：查询预算或 token 预算耗尽。

训练时 Gold 可以帮助判断“切换/停止是否过早”；dev/test 推理时绝不读取 Gold，只使用 Memory 中的可观察信号。由于第一次错误 STOP 会截断所有后续动作，最终必须按完整轨迹评测，而不能只报告逐状态分类准确率。

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

1. 从金时间线反推关键事件和证据文档，但把 Gold 保存在 private target，禁止进入学生输入；
2. 阶段一从空时间线或不同骨架前缀出发，生成覆盖时期/事件类型不同的候选 query；
3. 阶段二人为遮掉一个或多个日期、因果桥、事件要素或证据，得到可控 gap；
4. 教师输出 `state + memory → short thought → action → observation → updated state + updated memory`；
5. 在冻结语料中真实执行查询，按新 Gold 覆盖、证据支持、冗余和成本验收；
6. 从轨迹的每个中间状态提取局部样本，而不是每个主题只生成一条训练样本；
7. 加入“应继续搜索”“应切换阶段”“应停止”三类边界状态，并保证早停/过搜负例充足。

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
| SEARCH vs STOP | 仍有可检索高优先级缺口时 SEARCH；已充分时 STOP | 过早 STOP 或无收益继续 SEARCH | 边际 Gold 覆盖、支持度、冗余、成本 |
| 阶段切换 | 骨架稳定后 SWITCH_PHASE | 过早精修或长期停留粗搜 | 骨架覆盖、粗搜边际增益、可诊断缺口数 |
| Memory update | 忠实记录已覆盖/未覆盖、query 结果和 gap 状态 | 遗忘有效证据、重复历史 query、错误关闭 gap | 与工具 observation 的字段一致性 |
| VERIFY（可选扩展） | 有时间锚且证据蕴含的候选 | 仅复述、时间冲突或无支持候选 | 支持度、日期一致、证据 ID 合法性 |
| MERGE（可选扩展） | 正确 APPEND/UPDATE/DROP | 重复追加、错误覆盖或丢失证据 | 去重、要素覆盖、证据并集 |

可定义效用：

```text
U = 0.30·DateF1 + 0.25·AlignF1 + 0.20·EvidenceSupport
    + 0.15·Coverage - 0.05·Redundancy - 0.05·NormalizedCost
```

只有当两个候选效用差超过 margin 时才组成偏好对；接近的样本标为 tie 或丢弃，避免伪偏好噪声。

### 4.4 第二阶段如何利用 Gold 生成 DPO

第二阶段最适合利用 Gold，因为其任务本来就是“发现当前时间线缺了什么”。Gold 不直接教模型答案，而作为私有裁判构造同状态偏好：

1. 从完整 Gold 删除一个或多个事件/字段，形成 `timeline + gap_memory`；
2. 对同一状态生成 2–4 个 query 候选并在同一冻结检索器中回放；
3. 能找回未覆盖 Gold 事件/字段、证据有效且新增冗余低者为 chosen；重复、时间窗错误、只覆盖已知事件者为 rejected；
4. 尚有可达 Gold gap 时构造 `SEARCH > STOP`；覆盖达到阈值且继续搜索只有冗余时构造 `STOP > SEARCH`；
5. 骨架趋稳且存在可诊断细粒度 gap 时构造 `SWITCH_PHASE > 继续粗搜`；
6. 比较 Memory update 是否忠实记录工具返回、是否错误关闭 gap；
7. dev/test Gold 只进入离线评测器，不能进入教师标注或策略输入。

这一阶段不仅能生成 query 正负例，还能生成动作、阶段切换和 Memory 更新偏好，是 Gold 利用率最高的部分。DPO 的每对样本必须共享相同 state、Memory、预算和检索器，只改变一个局部决策。已有工作表明 DPO 可直接利用排序信号改善 query 生成，但本项目的排序信号改为 TLS 的边际事件覆盖与证据支持。[Query DPO](https://arxiv.org/abs/2505.19307)

### 4.5 当前数据是否足够

截至 2026-09-07，本地数据现状为：60 个主题、4,090 个对齐 Gold 事件、120 个 Gold 缺口任务，其中 train/dev/test 为 90/14/16；train 实际来自 43 个互斥主题。目前只有 1 条教师标注通过验收，已编译为 1 条完整 SFT 轨迹、4 条局部 DPO 对和 1 条 RL episode。

结论是：**足够验证数据链、Prompt、回放、编译和小规模训练代码，不足以直接声称模型已经学会可泛化的长程检索策略。**但不必立刻增加新数据集；先从现有每个 Gold 事件派生多个“中间状态”，数据扩展单位由“完整主题”改为“可回放决策状态”。

毕业设计采用三级规模，避免一开始追求过大的 5k–20k 完整轨迹：

| 级别 | 目标规模 | 用途 |
|---|---:|---|
| Pilot | 20–50 条完整 rollout；约 200–500 个局部偏好对 | 验证 LoRA/DPO 能收敛、schema 与 reward 无误 |
| 主实验 | 43 个 train topic 每个 5–10 个不同前缀/遮蔽 rollout；约 1k–3k query/动作偏好对 | 完成主比较和基本消融 |
| 扩展实验 | 从更多 Gold 前缀、预算和候选 query 扩到 6k–12k query 对、2k–4k SEARCH/STOP 对 | 时间允许时增强泛化和学习曲线 |

同一主题派生的状态高度相关，因此论文必须同时报告“独立主题数”和“状态/偏好对数”，不能把数千派生状态写成数千独立样本。推荐做 0.5k/1k/3k/6k 偏好对学习曲线；如果主实验规模已经显示稳定提升，就不把扩展规模作为毕业的硬前置条件。

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
- 先完成 20–50 条 pilot rollout 和 200–500 个局部偏好对，再扩展到 225–430 条多前缀 rollout、1k–3k 主实验偏好对；
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
- 策略：无效搜索率、重复查询率、正确 STOP 率、过早 STOP 率、阶段切换过早/过晚率；
- Memory：状态与工具 observation 一致率、错误关闭 gap 率、压缩率、STOP 后仍可到达的 Gold 事件数。

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

另做：无因果缺口、无事件要素缺口、单过滤 vs 双过滤、固定轮数 vs 学习停止、最终文本 DPO vs 局部决策 DPO。Memory 必做五组：无 Memory、拼接完整历史、单一 compact Memory、双 Memory 固定切换、双 Memory 学习切换。训练必做四组：只 DPO query、只 DPO SEARCH/STOP、二者联合、再加 Memory update。模块必做：通用 VERIFY/MERGE 与专门 SFT 模块对照。

### 7.3 数据切分

- 以“主题”为单位切分，禁止同一实体或事件跨 train/test；
- 增加时间截断：训练检索只能看到 cutoff 之前文档；
- 在线评测保存搜索快照；
- 对教师生成数据做近似匹配，排除与测试参考时间线高度重合的样本。

## 八、预期论文贡献与风险

可以写成三点贡献：

1. 提出面向 TLS 的**两阶段结构化检索 Memory**：阶段一记录时间覆盖与锚点，阶段二记录因果—要素缺口与证据状态；
2. 提出 Gold 驱动的**局部检索控制偏好数据**，联合学习 query、SEARCH/STOP、阶段切换和 Memory update；
3. 构建“专用可训练 Controller + 冻结通用 VERIFY/MERGE + 程序校验”的可复现完整流程，并联合评测时间线质量、证据忠实性和检索成本。

主要风险是“多模块集成但创新不足”。毕业设计不必把问题压缩到只剩一个动作，但应保持一个明确主问题：

> 在相同检索器、VERIFY/MERGE 执行器和预算下，两阶段结构化 Memory 加局部偏好优化，能否让 Search Agent 更好地形成时间线骨架、修复关键缺口并在合适时刻停止，从而提高覆盖、忠实性与成本效率？

这是一个完整系统问题，但变量仍可通过 Memory、动作训练和模块分工消融逐层验证，适合作为毕业设计主线。

## 九、建议的 8 周执行顺序

1. 第 1 周：复现 CHRONOS/Open-TLS 数据读取与评测；冻结闭域语料快照。
2. 第 2 周：实现事件 JSON、日期规范化、证据指针和基线检索。
3. 第 3 周：实现 coverage memory、gap memory、两阶段状态机，以及通用 VERIFY/MERGE 执行器。
4. 第 4 周：用 Gold 生成并回放 20–50 条 pilot rollout；完成 Controller SFT/QLoRA。
5. 第 5 周：构造 query、SEARCH/STOP、SWITCH_PHASE 和 Memory update 局部偏好对；训练 DPO。
6. 第 6 周：扩展到多前缀主实验数据；比较无 Memory、单 Memory、双 Memory 和固定/学习切换。
7. 第 7 周：完成模块消融、数据学习曲线、成本曲线、错误类型和人工忠实性评估。
8. 第 8 周：整理可复现配置、搜索快照、数据卡和论文初稿。

## 十、来源与研究边界

主要依据均为原论文或官方代码库：[MAS-TLS](https://aclanthology.org/2026.acl-long.1149.pdf)、[Temporal reasoning for timeline summarisation in social media](https://aclanthology.org/2025.acl-long.1362/)、[TISER](https://aclanthology.org/2025.acl-long.1358/)、[R2A-TLS](https://aclanthology.org/2025.findings-emnlp.40/)、[CHRONOS/Open-TLS](https://github.com/Alibaba-NLP/CHRONOS)、[DPO](https://papers.neurips.cc/paper_files/paper/2023/file/a85b405ed65c6477a4fe8302b5e06ce7-Paper-Conference.pdf)、[SimPO](https://papers.neurips.cc/paper_files/paper/2024/file/e099c1c9699814af0be873a175361713-Paper-Conference.pdf)、[ReAct](https://openreview.net/pdf?id=WE_vluYUL-X)、[Amber](https://aclanthology.org/2025.findings-acl.418/)、[DMPO](https://aclanthology.org/2024.emnlp-main.138/)、[SELF-RAG](https://openreview.net/pdf?id=hSyW5go0v8)、[Query DPO](https://arxiv.org/abs/2505.19307)。

需要注意：指定综述正文第 4 项是 R2A-TLS，但文末汇总表把第 4 项列为 TReMu；本方案按正文编号处理。MAS-TLS 与 R2A-TLS 的完整官方实现可得性不如 CHRONOS/TISER 明确，因此复现难度包含基于论文配置的推断。研究在四篇核心工作证据已经能覆盖“架构—训练—数据—成本—评测”后停止继续扩展，以避免用弱相关论文稀释主线。

## 十一、v4 两阶段真实执行标注协议（2026-09-07）

### 11.1 模型输入

SFT 的每一步输入统一包含 schema_version、dataset、topic、phase、events、memory、valid_actions，以及按步骤可选的 active_gap 和 tool_observation。events 是按 time、event_id 排序的完整当前时间序列。

budget、query_budget、queries_left、rounds_left 不进入模型输入和 SFT messages。执行器仍可保留不对模型可见的固定上限，防止 API 失控；它是环境终止条件，不是策略特征。

### 11.2 动作和输出

- SKELETON_EXPLORATION：SEARCH → VERIFY → MERGE 可重复，最后 STOP。
- GAP_MEMORY：进入 GAP_REFINEMENT 时分析当前有序事件，初始化时间断点、因果断点、关键事件/因素缺失和证据冲突。
- 单个 gap 必须先输出简短、具体、对学生可见的 thought，再执行 SEARCH。非空 query 表示真实检索；空字符串表示该 gap 无需检索并转到下一 gap。
- 非空查询后固定执行 VERIFY → MERGE。MERGE 只选 APPEND、UPDATE、DROP；UPDATE 继续由单独模型融合并经程序校验。MERGE 后再次更新 GAP_MEMORY，记录 resolved_gap_ids 和新出现的 gaps。
- thought 不得出现 Gold、参考答案、标准答案等教师侧术语，必须描述可观察的时间断裂、因果缺失、关键因素缺失或停止原因。

### 11.3 重新定义的标注来源

第一阶段完全采用 DeepSeek 在冻结语料上的真实 rollout，不使用 Gold：模型实际生成 query，冻结的 BM25 + 多语言 dense 混合检索器返回文档，DeepSeek 实际 VERIFY、MERGE，并自己 STOP。第二阶段采用“私有教师信息、公开学生标签”的蒸馏边界：教师可以比较第一阶段结果与 train Gold 来初始化 gap、选择搜索方向，但写入 SFT 的模型输入和输出都不包含 Gold 字段或教师对齐；VERIFY/MERGE 只看检索证据与当前时间线。教师事件 ID 对齐单独写入 teacher_alignment.private.json，不得进入学生训练文件。

### 11.4 Egypt v4 pilot 结论

真实试跑生成 17 个动作标签、16 个最终事件和 17 条 SFT 样本，动作序列完整包含两个阶段及两轮 gap 闭环。自动验收确认全部 SFT 输入无预算字段、事件有序、私有对齐未泄漏。但当前配置只处理 2 个 gap，201 个对齐事件只语义匹配 1 个，Gold event recall 为 0.50%，Gold date recall 为 6.56%。因此该 pilot 只证明流程可执行，不能证明完整覆盖；正式标注应把 201 个事件按时间段形成未覆盖候选批次，逐批 GAP_MEMORY 和回放，直到无开放 gap 或覆盖增益收敛。

## 十二、v2 闭域混合检索设计（2026-09-08）

### 12.1 问题与边界

原 BM25 只擅长词面重合。查询若使用同义表达、上位概念、中文描述英文语料，或没有复述文章标题中的实体词，即使相关证据存在于冻结语料中也可能无法进入 VERIFY，后续模型再强也不能恢复被漏掉的证据。因此检索器被视为独立实验变量并冻结；Gold 只用于离线评测和 train 教师标注，不进入运行时查询、向量或排序。

### 12.2 检索流水线

1. 对冻结文章建立 SQLite FTS5/BM25 文档索引，保留强实体词、日期词和精确短语匹配；
2. 将标题与正文切成 600 字符、重叠 100 字符的 passage，用多语言 BERT 句向量模型 paraphrase-multilingual-MiniLM-L12-v2 编码并 L2 归一化；
3. Controller 每个 query 同时检索 BM25 与 dense 通道，各自取 max(50, top_k × 5) 个候选；
4. 不直接相加 BM25 与余弦分数，而用 weighted RRF 合并排名，默认两通道权重均为 1、rrf_k=60；
5. 按正文哈希去重，并施加很弱的同日重复惩罚，避免某一突发事件的转载文章占满 top-k；
6. 返回文档 ID、日期、证据片段、BM25/dense 原始排名及分数，供 VERIFY、多结果 MERGE、轨迹回放和错误分析使用。

检索结果可以一次处理多个候选：VERIFY 对每篇证据独立判断支持、冲突或无关；MERGE 再按 APPEND、UPDATE、DROP 逐项处理。dense 命中的 passage 只是定位证据，必要时通过文档 ID 读取受限长度的原文，避免把整篇长文送入模型。

### 12.3 实验与消融

至少报告四组同查询、同候选数量实验：BM25；dense；BM25+dense RRF；BM25+dense RRF+去重/时间多样化。检索层报告 Evidence Recall@k、Gold-date Recall@k、MRR/nDCG、唯一日期数和重复率；系统层继续报告 Date-F1、ROUGE、证据支持率、查询数和墙钟时间。参数只在 train/dev 调整，test 只执行一次；若混合检索只提高候选召回却未提高最终时间线，应继续定位 VERIFY、MERGE 或 STOP，而不能把问题错误归因于 Retriever。

### 12.4 与核心创新的关系

混合检索器不是需要训练的 Agent 动作，也不作为论文唯一创新点；它是更公平的冻结环境，防止弱 BM25 掩盖两阶段 Memory 和 SEARCH/STOP 学习的效果。论文核心仍是 Controller 根据 coverage memory 与 gap memory 先形成简短检索意图，再学习 query、SEARCH/STOP、阶段切换和 Memory 更新；VERIFY/MERGE 可继续交给冻结通用模型。

### 12.5 T17/mj pilot 对方案的修正

真实中文查询“迈克尔杰克逊 去世 葬礼 纪念”在英文闭域语料上使 BM25 返回 0 篇，而 dense/hybrid 返回完整 top-20，证明语义通道解决了证据不可达问题。但英文强词面查询中，初始 1:1 RRF 和试验性 2:1 RRF 的 publication-date 代理覆盖都没有稳定超过 BM25。这一结果要求把“增加 dense”与“融合排序已最优”分开论证：

- 论文可以主张 hybrid 扩大跨语言和同义查询的候选可达性；
- 不能仅凭单主题发布时间覆盖主张最终 Evidence Recall 提升；
- 固定 RRF、词面充分性门控、BM25 保底配额应成为三组检索消融；
- 权重和门控阈值只能在 train/dev 的 event–passage 相关性标注上确定；
- test Gold 不用于融合调参，最终再联合报告检索指标和 TLS 指标。

因此 Retriever 的目标从“强制每次融合两个通道”修正为“两个通道均可用、融合策略冻结且由开发集选择”。Controller 仍只学习何时搜、搜什么和何时停，不学习检索器内部权重，避免把检索排序与 Agent 策略混成无法解释的共同变量。

### 12.6 v3 全量索引落地与工程取舍（2026-09-09）

全量滑窗估算约产生 84.2 万个 passage 向量，在当前 CPU 环境中构建时间超过一天，不适合作为毕业设计的默认复现配置。v3 因此采用“全文 BM25 + 每文档一个分层语义向量”：稠密表示由标题和正文开头、中部、结尾的受限片段组成，既保持全文精确词面可检索，又将稠密向量数压缩到文章数。编码后端改为动态 INT8 AVX2 ONNX，向量以 float16 保存；256 条真实 passage 的编码吞吐由 PyTorch 的 7.62 条/秒提升到 13.05 条/秒，向量余弦一致性均值为 0.9932。

索引按 topic 分片并采用原子发布，根 manifest 记录已完成分片。中断后可以跳过有效分片继续构建，同时检索器根据 search engine 中的 topic 只加载对应向量，避免载入整个数据集。T17、Crisis、Entities 已完成 9、4、47 个 topic，共 72,959 个 float16 向量；校验确认向量数量与 BM25 文档数量逐主题相等，形状均为 384 维，且全部有限、非零。三个稠密集合约占 111.3 MiB；持久进程中的热查询实测为 0.03--0.57 秒。

这一压缩属于工程默认值而不是未经验证的效果结论。完整滑窗和每文档单向量必须作为检索消融对比；已有 T17/mj 发布时间代理实验中，紧凑表示没有比完整滑窗更差，但该代理不等价于 Evidence Recall。最终论文仍需在 train/dev 构造 event--passage 相关性标注，报告真正的 Evidence Recall@k，并冻结配置后评测 test。

### 12.7 dev效果验证对融合策略的修正（2026-09-10）

在既定dev划分的7个主题、367个对齐Gold事件上，以事件摘要作为Evaluator侧oracle query进行日期代理评测。修复BM25长查询中的停用词和无限OR问题后，总评测耗时由709.83秒降至493.96秒；BM25的±2天Hit@10为64.03%，Dense为50.95%，固定1:1 RRF为63.22%。Hybrid返回日期更丰富，也在部分人物主题上补到BM25漏掉的事件，但总体没有超过BM25。因此论文不能预设“Hybrid必然优于BM25”，必须将词面检索保底。

缓存排名的配额消融中，top-10保留7个BM25并补3个Dense时，exact Hit@10由42.51%升至44.14%，±2天Hit@10由64.03%升至64.58%；但逐事件精确二项检验分别为p=0.210和p=0.815，尚不显著。该策略只作为更大qrel的候选，不替换当前冻结的1:1 RRF基线。

在已授权的David Bowie片段上完成63条LLM qrel，其中6条为直接证据，只有1/3个抽样事件在候选池中存在正例。该resolved事件的BM25、Dense、Hybrid Recall@10分别为83.3%、50.0%、66.7%。样本过小不能作统计结论，但再次表明强词面事件需要BM25保底。后续融合冻结顺序调整为：先扩大dev event--passage qrel和pool depth，再比较BM25、Dense、1:1 RRF与7:3配额；最后依据Evidence Recall@k而不是发布日期代理选择配置。跨语言中文查询仍保留Dense回退，因为T17/mj实测BM25为0而Dense可返回完整top-20。

获得Yemen/MJ片段明确授权后，qrel扩展到三主题183条判断、16条直接证据和7个resolved事件。合并后的BM25、Dense、1:1 RRF Recall@10分别为76.19%、60.71%、84.52%，说明日期代理确实低估了语义通道的证据互补价值。在同一qrel上，BM25 7 + Dense 3配额取得94.05% Recall@10和0.6564 nDCG@10，高于1:1 RRF的84.52%和0.6239；RRF的MRR 0.436略高于配额的0.425。工程上将7:3实现为可选实验模式，保持v3不变，待每主题10--20个事件、pool depth 20的更大dev qrel确认后再冻结。

扩展版已完成标注与评测：Yemen/MJ各10个事件、David Bowie全部9个事件，pool depth 20，共1,124个唯一判断；从旧run精确复用143条，本轮新增981条、50个完成批次。标签为Grade 2直接证据104条、Grade 1上下文134条、Grade 0无关886条，29个事件中21个resolved。调用日志覆盖全部981条新增判断，成功响应累计585,618 tokens；执行期间未出现余额不足。中途一次 `http.client.IncompleteRead` 暴露出响应读取异常未进入重试层，修复后从987条缓存断点继续，未重复发送已完成候选。

在扩展qrel上，BM25、Dense和1:1 RRF的Recall@20分别为79.07%、63.14%和88.01%，RRF的MRR也最高（0.5026）。top-20配额消融中，BM25 16 + Dense 4的Recall@20为87.84%、nDCG@20为0.7057；BM25 14 + Dense 6的nDCG@20最高（0.7106），但Recall@20降至87.10%。因此小pilot中“7:3配额召回明显优于RRF”的结论不再成立：当前默认仍保留1:1 RRF以优先覆盖，偏BM25配额用于排序质量消融。该评测使用Gold事件摘要作为Evaluator侧oracle query且仅有三个dev主题，后续必须用Search Agent实际生成query复测，不能把检索器上界直接写成Agent性能。

### 12.8 真实query回放对训练重点的修正（2026-09-10）

将现有DeepSeek轨迹中的真实SEARCH query重新提交给冻结v3混合索引，并用同一qrel评测。Yemen exploration和CHRONOS query baseline各3轮，在当前7个resolved抽样事件上均未命中Grade 2；但两组返回文档分别只有7/55和2/36已进入qrel池，因此这是带pool bias的下界而不是完整失败结论。David Bowie完整Agent的4轮查询覆盖2/5个resolved事件，恢复7/42个Grade 2证据对；两个新事件都由第一轮覆盖，后续三轮只有2个证据对增益且没有新事件覆盖。

该结果验证了研究方案应把Controller query和SEARCH/STOP作为核心训练目标，而不是继续只优化Retriever内部融合。oracle query下RRF Recall@20为88.01%，真实query覆盖却明显更低，说明可达证据没有被正确检索意图激活。DPO/SFT数据应记录每轮新增事件、直接证据对增益、未解决gap和重复覆盖：高边际收益query作为chosen；相同状态下错年代、过窄或重复覆盖query作为rejected；连续低增益且gap已关闭时STOP为chosen，否则SEARCH为chosen。

为消除真实query回放的pool bias，已标注Yemen与David Bowie共154篇唯一候选、1,408个缺失event–document判断，每篇文本最大698字符。与原qrel合并后共有2,532条唯一判断；新增标签为Grade 2七条、Grade 1十五条、Grade 0一千三百八十六条。执行中通过长退避解决TLS EOF和连接拒绝，通过隔离单篇文档解决多事件矩阵漏键；最终15个成功批次、剩余0，未出现余额不足。成功响应审计累计203,048 tokens，但失败响应的真实计费仍以服务商控制台为准。

消除unjudged后，Yemen exploration与CHRONOS baseline均覆盖2/7个resolved抽样事件，Grade 2证据对召回均为2/51；David Bowie覆盖2/5，Grade 2证据对召回为10/45。David Bowie第一轮贡献7个直接证据对并覆盖全部最终命中事件，后续三轮只补同事件证据，最后一轮零增益。奖励层因此采用分层边际信号：新事件覆盖为最高权重，同事件Grade 2证据补强次之，Grade 1上下文再次之，重复或零增益查询施加成本；STOP标签还必须检查gap是否关闭，避免把“当前query不好”误标为“无需继续搜索”。MJ仍需单独生成一条不含Gold泄漏的两阶段真实rollout。

### 12.9 时间范围检索与 Memory 联动（2026-09-11）

检索层现已统一支持发布日期范围，参数为 date_from、date_to、date_filter_mode 和 date_soft_penalty，并贯通 BM25、dense、hybrid、CLI、轨迹缓存及重放。none 完全保持旧行为；hard 在各模态候选截断前排除区间外文档；soft 扩大候选池，对区间外证据施加可审计的排名惩罚但不删除，避免漏掉事后回顾、迟发报道和背景材料。返回结果记录模式、边界、是否命中区间和软惩罚值。

必须区分“文档发布日期”和“事件发生日期”。闭域索引直接拥有的是前者，因此 hard 只能作为精确时间局部检索或受控消融，不应成为所有 Agent 查询的默认值。两阶段 Controller 的建议策略是：

1. SKELETON_EXPLORATION 使用 none，先建立主题全局时间骨架；
2. GAP_REFINEMENT 从 active_gap 与 gap memory 产生目标窗口，默认使用 soft；
3. 只有 gap 已有明确左右时间锚点、且任务要求局部证据时才选择 hard；
4. 时间边界、模式和查询一起写入 SEARCH action，使 SFT/DPO 能学习“搜什么、搜哪段时间、何时停止”；
5. 对同一状态构造 none/soft/hard 候选并按新增 Gold 事件覆盖、Grade 2 证据增益、重复率和检索成本打分，避免只凭教师偏好选择模式。

实现验证包括五类新增回归：BM25 在 top-k 前 hard 过滤、日期参数拒绝歧义/逆序、soft 保留区间外证据、dense/hybrid hard 过滤、带日期配置的轨迹重放。完整测试为 85 passed。冻结 Crisis 混合索引上的真实查询“Mubarak resigns Egypt”在 2011-02-01 至 2011-02-28 hard 模式返回 10/10 条区间内结果。下一步应在现有 2,532 条 qrel 上做 none/soft/hard 同状态消融，再把最优时间模式纳入 query 与 SEARCH/STOP 的 SFT/DPO 标注。

初步同状态消融已完成：29个dev事件、±14天窗口、soft_penalty=0.5时，none、soft、hard的Recall@20分别为0.8472、0.8761、0.7221，nDCG@20分别为0.6685、0.7758、0.6846；none与旧缓存29/29完全一致。初步实验存在soft的144条、hard的231条返回关系未标注，以下补标后的统一评测替代该组初步数字。

### 12.10 时间范围统一池复评（2026-09-11）

经用户确认授权，221篇候选按实际事件检索关系补标250对，28批全部完成。新增13条Grade 2、35条Grade 1、202条Grade 0，合并qrel共2,782条。全部调用首次通过，记录184,568 tokens，未出现余额不足。独立审计确认新增关系精确对应授权集合、旧标签保留、无重复、87组排名与补标前一致，三种模式本次返回关系全部已标注。

共同qrel下，none/soft/hard的Recall@20为0.8072/0.8700/0.7273，nDCG@20为0.6365/0.7733/0.6931，MRR为0.5026/0.6529/0.6494。Recall与nDCG按21个存在直接证据的事件平均，MRR按全部29个事件平均。soft相对none提高6.28个百分点召回和0.1368 nDCG，但两者Success@20同为21/29，体现证据覆盖与排序收益，并未增加最终命中的抽样事件数。

三主题均有soft收益，Yemen召回提高最明显，David Bowie改善较小。实验支持在gap阶段尝试soft，hard保留为可选动作；尚未切换实际Agent的默认检索模式。query与窗口中心仍为Evaluator侧Gold输入，不能将这些结果视为真实Controller性能。soft也扩大了候选池，需以相同候选深度对照分离时间偏置贡献。当前仅消除指定排名池的缺标，尚未穷举全语料正例，亦未完成窗口调参、人工复核或统计显著性验证。后续使用train轨迹构建SFT/DPO，不将本轮dev判断直接混入训练。
