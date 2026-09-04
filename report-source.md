# 面向时间线摘要的 Search Agent：深度研究底稿

- 日期：2026-09-04
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
