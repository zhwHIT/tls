# 面向时间线摘要的 Search Agent：深度研究底稿

- 首版日期：2026-09-04；本次深度研究更新：2026-09-07
- 受众：计划开展 Timeline Summarization（TLS）与检索智能体研究的研究者
- 范围：指定综述正文“核心方法论文”第 1–4 项，即 MAS-TLS、Temporal reasoning for timeline summarisation in social media、TISER、R2A-TLS
- 假设：目标是做开放域新闻时间线生成；优先考虑可公开复现、单机或少量 GPU 可训练，并形成有清晰消融实验的论文工作

## 直接结论

最值得做的不是再堆叠一个多智能体流水线，而是训练一个“时间线状态驱动的搜索策略模型”：维护可验证的事件图和缺口表，让模型在每轮从 SEARCH、VERIFY、MERGE、STOP 中选择动作。采用两阶段后训练：先用 SFT 学习结构化的时间推理、缺口反思和工具调用轨迹，再用 DPO/SimPO 对同一状态下的好坏查询、好坏证据选择和好坏停止决策进行偏好优化。多智能体只作为并行候选生成器，最终选择策略应由一个可训练的轻量 Supervisor 控制。

## 证据与判断

1. MAS-TLS 将 TLS 组织为主编、记者、交叉审查、裁决和预算调度；默认 Qwen3-32B、5 个子智能体、双 A800，并报告明显 token/延迟节省。该工作证明“分工和预算控制”有效，但完整规模复现成本高，适合借鉴其控制面而非照搬模型规模。
2. Song 等使用 NarrativeReason 训练时间推理教师，并将表征知识蒸馏到同时学习时间线摘要的学生；原实验为 Llama-3-8B 教师、Phi-3-mini-4k-instruct 学生和 LoRA。它说明时间推理可以作为可迁移中间能力，但神经元级 NST/PKT 蒸馏工程耦合较强，未必是最简洁的 Search Agent 主线。
3. TISER 把输出分成 reasoning、timeline、reflection、answer，并通过仅保留最终答案正确的合成轨迹做 SFT；原实验用 Qwen2.5-7B/Mistral-7B，并使用 8 张 A100。它最适合转化成轨迹数据协议，但其 token 开销约由 3.41 增至 94.74，说明反思必须有停止规则。
4. R2A-TLS 用因果链缺口做时间点补全，用 FrameNet 风格要素缺口做事件补全，再触发定向深检索。其 Open-TLS 实验显示首轮 20 篇文档后继续盲目扩大检索收益趋于饱和；LoRA 时间点补全只用 352 个样本训练 Qwen2.5-7B，单张 A100 40GB，证明核心部件可低成本复现。

## 主张—来源账本

- MAS-TLS 方法、数据集、模型与硬件：Wang et al., “Agent Newsroom: Efficient Chronological Report Generation via Dynamic Multi-Agent Collaboration,” ACL 2026, https://aclanthology.org/2026.acl-long.1149.pdf
- NarrativeReason 与蒸馏配置：Song et al., “Temporal reasoning for timeline summarisation in social media,” ACL 2025, https://aclanthology.org/2025.acl-long.1362.pdf
- TISER 轨迹、SFT、模型与 token 开销：Bazaga et al., “Learning to Reason Over Time,” ACL 2025, https://aclanthology.org/2025.acl-long.1358.pdf
- R2A-TLS、Open-TLS 与 LoRA 配置：Bao et al., “R2A-TLS,” Findings of EMNLP 2025, https://aclanthology.org/2025.findings-emnlp.40.pdf
- Open-TLS 数据和基线代码：Alibaba-NLP/CHRONOS, https://github.com/Alibaba-NLP/CHRONOS
- DPO：Rafailov et al., “Direct Preference Optimization,” NeurIPS 2023, https://papers.neurips.cc/paper_files/paper/2023/file/a85b405ed65c6477a4fe8302b5e06ce7-Paper-Conference.pdf
- SimPO：Meng et al., “SimPO,” NeurIPS 2024, https://papers.neurips.cc/paper_files/paper/2024/file/e099c1c9699814af0be873a175361713-Paper-Conference.pdf

## 局限

MAS-TLS 和 R2A-TLS 未在论文页提供同等完整的官方实现入口；复现难度判断包含基于论文硬件、API、数据发布状态的推断。Open-TLS 只有 50 个主题，不适合直接作为大规模训练集，需要从旧 TLS 数据、新闻归档或合成扰动中构造训练轨迹，并严格按主题和时间切分防止泄漏。

## 2026-09-04 P0 实施更新

- Crisis 快照 `ece08f344cc94933` 已通过 dataset-specific 全量校验：4 个主题、17,573 篇压缩文章、22 条参考时间线。
- 已实现 Open-TLS/闭域数据的批量预测目录评测和跨主题 macro mean；50 主题 gold 回灌烟测通过。该烟测仅验证管线，不构成模型实验结果。
- 结果文件现记录 CHRONOS commit、快照 ID/manifest 哈希、预测 SHA-256、指标后端、Python/平台和耗时。
- WSL/Linux 尚未安装，论文可比的 `original` Perl ROUGE 仍待 Linux 环境验证；Windows `reimpl` 结果只用于开发回归。
- 详细命令、文件职责与结果见 `进度/06_P0完整性与批量评测实施记录.md`。

## 2026-09-04 P1 实施更新

- T17 与 Entities 的官方数据入口为 news-tls README 指向的共享 Google Drive 总目录：`https://drive.google.com/drive/folders/1gDAF5QZyCWnF_hYKbxIzOyjT6MSkbQXu?usp=sharing`。下载现已暂停，残缺数据不进入正式实验。
- 已基于 Crisis 快照建立 SQLite FTS5/BM25 索引，共 17,573 篇文档；索引绑定 snapshot ID 与 manifest 哈希。
- 已实现兼容 `search_engine="crisis <topic>"` 的闭域多查询适配器，并完成真实 Egypt 查询烟测。
- 下一步是固定实验配置并缓存可重放的逐轮 Search Agent 轨迹。

## 2026-09-04 T17/Entities 数据更新

- 用户提供的 T17 与 Entities ZIP 均通过 ZIP CRC；分别具有 27 和 141 个必要文件。
- 全量语料校验通过：T17 为 9 主题、4,203 篇文章、19 条参考时间线；Entities 为 47 主题、51,183 篇文章、47 条参考时间线。
- 已生成并复验内容寻址快照：T17 `2704b6b058774e15`，Entities `25ac73e52bc93b3b`。
- 此前关于两者下载不完整的记录作为历史过程保留，但已不再代表当前可用数据状态。

## 2026-09-04 P1 轨迹更新

- Crisis、T17、Entities 三套固定快照均已建立 SQLite FTS5/BM25 索引，共 72,959 篇文档。
- 检索配置固定为 top-k 20、最多 3 轮、连续 2 轮无新增文档停止，并按 doc ID 去重。
- 搜索轨迹现记录完整状态转移、结果、索引 SHA-256 和快照元数据，可以逐轮确定性重放。
- 真实 Crisis/Egypt 三轮轨迹重放一致；模型与 prompt 尚未选择，当前结果只属于检索基建，不属于生成模型基线。

## 2026-09-07 两阶段 Memory Search Agent 深度研究更新

### 可行性结论

该方向适合作为毕业设计，工程上比“一个模型同时学习 SEARCH/VERIFY/MERGE/STOP 全部能力”更容易落地。完整运行轨迹仍保留四动作，但核心可训练 Controller 只负责阶段相关的短结构化思考、Memory update、query、SEARCH/SWITCH_PHASE/STOP；VERIFY、MERGE 和 Writer 先由冻结通用模型执行，再由程序校验证据 ID、日期、操作类型和证据并集。

两阶段流程本身有 R2A-TLS 的直接可行性证据；显式 Memory 驱动迭代 query/stop 有 Amber 与 MemSearcher 的直接可行性证据；DPO 学 query 有 Query DPO 的直接证据；局部状态 DPO 比整轨迹普通 DPO 更稳妥，DMPO 说明了多轮轨迹长度和动态分布问题。因此本项目的组合增量定义为：TLS 专用的双阶段结构化 Memory、Gold 驱动的局部检索控制偏好，以及模块化冻结执行器。

### 关键证据账本

- R2A-TLS：initial retrieval→reflection→deep retrieval；用因果链与 FrameNet 要素发现缺口，再生成定向 query。来源：https://aclanthology.org/2025.findings-emnlp.40/
- CHRONOS：iterative self-questioning、检索与逐轮时间线刷新，支持多轮 TLS 检索基线。来源：https://aclanthology.org/2025.findings-naacl.248/
- Amber：从空 Memory 开始迭代更新，依据 Memory 充分性继续改写 query 或停止。来源：https://aclanthology.org/2025.findings-acl.418/
- MemSearcher：compact memory 避免完整交互历史不断增长，并联合推理、检索和记忆管理。来源：https://aclanthology.org/2026.findings-acl.736/
- ReAct：交错 reasoning、action、observation，支持检索前先做行动规划。来源：https://openreview.net/pdf?id=WE_vluYUL-X
- DPO：同一 prompt 下使用 chosen/rejected 直接优化偏好，无需单独奖励模型。来源：https://arxiv.org/abs/2305.18290
- DMPO：普通 DPO 直接用于动态多轮轨迹存在分布与长度问题，支持本项目采用同状态局部 DPO。来源：https://aclanthology.org/2024.emnlp-main.138/
- Query DPO：用真实排序信号构造 query 偏好，证明 DPO 可以改善查询生成。来源：https://arxiv.org/abs/2505.19307
- SELF-RAG：将是否检索和证据相关/支持判断拆成可学习标记，支持动作模块化。来源：https://openreview.net/pdf?id=hSyW5go0v8
- WebGPT：行为克隆加偏好反馈可训练浏览动作，并用引用降低事实评测歧义。来源：https://openai.com/index/webgpt/

### 与已有工作的边界

- 不声称“两阶段检索”本身首次提出，因为 R2A-TLS 已有相同总体路线。
- 不声称“Memory 指导下一 query 或 stop”本身首次提出，因为 Amber、MemSearcher 已有相近机制。
- 本毕业设计仍可把两阶段完整框架作为系统创新，具体新增和验证的是两种 TLS 专用 Memory schema、显式阶段切换、Gold 边际覆盖偏好、局部搜索控制 DPO，以及专用 Controller 与通用执行模块的协作。
- 第一阶段不是零思考，而是不做昂贵因果反思；只分析时间覆盖、锚点、事件类型和最近检索增益。第二阶段才分析因果断点、缺失要素、证据冲突和未覆盖 Gold-independent gap。

### 当前数据量审计

- 冻结数据：Crisis、T17、Entities，共 60 个主题、4,090 个对齐 Gold 事件。
- Gold 任务模板：120 个，train/dev/test 为 90/14/16；train 来自 43 个互斥主题。
- 真正完成的教师数据：目前仅 1 条 accepted label，编译为 1 条完整 Tool-SFT、4 条局部 DPO、1 条 RL episode。
- 判断：目前足够做数据链和训练代码 pilot，不足以宣称策略泛化。120 个模板不能写成 120 条已完成 LLM 标注数据。
- 可行扩展：每个 train topic 生成 5–10 个不同前缀、遮蔽和预算的 rollout，从每轮抽取同状态局部决策；主实验先达到 1k–3k 偏好对，6k–12k query 对与 2k–4k SEARCH/STOP 对作为时间允许时的扩展目标。
- 论文必须同时报告独立 topic 数和派生 state/pair 数；同一主题的派生状态不能冒充独立主题。

### Gold 在第二阶段的用途

Gold 仅作训练期私有教师和离线裁判，不进入学生 prompt。通过删除一个或多个 Gold 事件/字段生成 gap state；候选 query 在同一冻结检索器中真实回放，以边际 Gold 覆盖、证据支持、冗余和成本排序。由此构造：

- 能补缺口的 query 优于重复、过宽、时间窗错误或只找回已知事件的 query；
- 有可达高优先级缺口时 SEARCH 优于 STOP；
- 已充分且继续检索只增冗余时 STOP 优于 SEARCH；
- 骨架稳定且出现可诊断细缺口时 SWITCH_PHASE 优于继续粗搜；
- 忠实记录已覆盖/未覆盖和工具结果的 Memory update 优于遗忘、幻觉或错误关闭 gap 的更新。

### 最小消融集合

1. 无 Memory、完整历史拼接、单 compact Memory、双 Memory 固定切换、双 Memory 学习切换。
2. 只 DPO query、只 DPO SEARCH/STOP、联合两者、再加 Memory update。
3. 通用 VERIFY/MERGE 与专门 SFT VERIFY/MERGE。
4. Prompt-only API、Controller SFT、Controller SFT+DPO。
5. 指标除 Date-F1/Align 外，加入每轮新增 Gold 覆盖、证据支持、冗余、query/token 成本、过早 STOP、切换过早/过晚和 STOP 后仍可到达的 Gold 数。
