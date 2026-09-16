# Gold 监督与 LLM 标注数据构建逻辑

> 更新时间：2026-09-07  
> 参考：`时间线_Search_Agent_框架研究方案.md`  
> 状态：Gold 任务、隔离边界、LLM 标注协议和训练数据编译器已实现；本轮未调用外部 LLM。

## 1. 为什么重构

旧版种子数据只取第一条参考时间线的中位事件，没有充分利用多参考 Gold；只检查 task ID，未检查跨数据集同义主题；教师调用、真实检索、验收和最终文件生成也混在一个脚本中。此次把链路拆为可审计、可断点恢复的阶段，并强制隔离 dev/test Gold。

## 2. 完整数据流

```text
冻结语料与 Gold timelines
  → 多参考事件对齐和共识聚类
  → 选择监督目标并构造四类缺口
  → topic/语义组切分
  → policy task 与 private target 分文件
  → 仅 train：LLM 标注 chosen/rejected SEARCH
  → 在冻结索引中真实执行查询
  → 仅 train：LLM 标注 VERIFY/MERGE/STOP
  → 程序校验 JSON、查询、日期、证据 ID、偏好间隔
  → 确定性编译 Tool-SFT、局部 DPO、RL episode
  → dev/test private Gold 只进入离线评测器
```

轨迹遵循研究方案的 `state → reflection → action → observation → updated_state`，核心动作固定为 `SEARCH / VERIFY / MERGE / STOP`。

## 3. Gold 如何利用

### 3.1 多参考对齐

同一 topic 的全部参考时间线参与聚类：日期最多相差 2 天，摘要 token Jaccard 阈值为 0.2，同一参考中的事件不能互相合并。聚类保存日期中位数 `canonical_date`、`accepted_dates`、文本变体、参考覆盖数和共识比例。目标选择优先参考覆盖高、信息完整的事件。

### 3.2 四类缺口

- `MISSING_DATE`：保留内容，遮蔽日期；
- `CAUSAL_BRIDGE`：遮蔽连接前后事件的目标；
- `MISSING_ROLE`：保留骨架，遮蔽参与者/角色；
- `EVIDENCE_CONFLICT`：给出冲突候选，要求搜索核验。

当前 120 个任务中四类各 30 个。Gold 只决定遮蔽目标、允许答案与验收条件，不直接放进学生可见状态。

### 3.3 Gold 的职责

1. 生成需要恢复的事件或字段；
2. 约束训练集教师标注；
3. 验收 VERIFY 日期和语义目标；
4. 作为 dev/test 的离线评测标准。

## 4. 隔离和防泄漏

生成目录 `chronos_repro/artifacts/tisa_gold_tasks_v2/` 包含：

```text
train/policy_tasks.jsonl
train/private_targets.jsonl
dev/policy_tasks.jsonl
dev/private_targets.jsonl
test/policy_tasks.jsonl
test/private_targets.jsonl
tisa_gold_tasks_v2_manifest.json
```

`policy_tasks` 是 Agent 可见输入，`private_targets` 是教师或评测器私有目标。标注入口只接受同一 `train` 目录内的成对文件；只要路径不是 train、行内 split 不是 train、ID/topic 不匹配，或 policy 中出现 private Gold 字段，就直接拒绝。

跨数据集采用两层检查：规范化同名检查，以及显式语义组检查。显式组包括 Egypt、Libya、Syria/Bashar、MJ/Michael Jackson、Julian Assange/WikiLeaks。

本轮实际检出旧配置中 Crisis `syria` 属于 dev、T17 `syria` 属于 test 的语义泄漏。现已改为 Crisis `yemen` 属于 dev；Crisis/T17 `syria` 与 Entities `Bashar_al-Assad` 统一属于 test。

## 5. LLM 与程序的分工

LLM 标注：短反思、强/弱 SEARCH query、证据支持的 VERIFY、MERGE、STOP，以及四个动作的局部 chosen/rejected 和偏好分数。

程序负责：Gold 对齐和切分、冻结 BM25 检索、查询长度与 Gold 抄写率、证据 ID、允许日期、偏好 margin、工具消息编译、输出哈希。这样保留 LLM 的语义判断，同时不允许其伪造工具结果、数据切分或评测结论。

## 6. 两阶段教师标注

阶段 A 中，教师读取 train policy 和对应 train private target，生成 chosen/rejected query；程序真实执行两个查询。查询相同、长度越界、强查询无结果或复制 Gold 摘要过多时要求修复。

阶段 B 中，教师看到真实返回的文档标题、日期和最多 500 字符片段，只能引用其中的文档 ID，并标注 VERIFY/MERGE/STOP。程序要求 VERIFY 至少一条有效证据、日期属于 `accepted_dates`、摘要非空、偏好分数在 `[0,1]` 且 margin 至少 0.2。

格式或标签问题最多定向修复 2 次；网络瞬态错误由客户端重试；余额不足立即停止。确定性认证或请求错误在分析后视为无法通过原请求重试解决。

## 7. 最终训练数据

`compile_tisa_training_data.py` 将验收标签确定性编译为：

- `tisa_tool_sft_v2.jsonl`：完整 SEARCH→VERIFY→MERGE→STOP 轨迹；
- `tisa_local_dpo_v2.jsonl`：每轨迹 4 个同状态局部偏好对；
- `tisa_rl_episodes_v2.jsonl`：显式 state/action/observation/reward/terminal；
- `tisa_curriculum_v2.jsonl`：时间能力 40%、缺口到查询工具链 40%、合并/证据/停止 20%。

局部 DPO 不比较整条长轨迹，避免把回答长度误当质量。

## 8. 当前结果

| 项目 | 结果 |
|---|---:|
| 数据集 | Crisis、T17、Entities |
| 主题 | 60 |
| 对齐后 Gold 事件 | 4,090 |
| Gold 监督任务 | 120 |
| train / dev / test | 90 / 14 / 16 |
| 四类缺口 | 各 30 |
| 跨数据集语义组 | 5 组，全部通过 |
| 重复生成 | manifest SHA-256 一致 |
| 自动测试 | 38 passed |
| 本轮外部 LLM 调用 | 0 |

## 9. 文件作用

- `configs/tisa_gold_supervision_v2.json`：数据快照、索引、切分、对齐阈值和语义组。
- `configs/tisa_llm_annotation_v2.json`：模型、top-k、修复次数、偏好 margin 和错误策略。
- `src/chronos_repro/gold_supervision.py`：Gold 对齐、选择、遮蔽、查询和切分校验。
- `src/chronos_repro/annotation_boundary.py`：train-only 教师输入边界。
- `src/chronos_repro/tisa_data.py`：工具 schema、序列化和标签验证。
- `scripts/prepare_tisa_gold_tasks.py`：不调用 LLM 的 Gold 任务准备器。
- `scripts/annotate_tisa_train.py`：仅 train 的两阶段 LLM 标注，逐条 checkpoint。
- `scripts/compile_tisa_training_data.py`：编译 SFT/DPO/RL 数据。
- `tests/test_gold_supervision.py`：Gold、遮蔽、抄写和跨数据集泄漏测试。
- `tests/test_annotation_boundary.py`：dev/test 和 private Gold 泄漏拒绝测试。
- `tests/test_tisa_data.py`：工具调用、证据 ID、偏好分数测试。

## 10. 下一步

先做少量 train pilot。调用前需明确授权发送范围：train policy state、对应 train private Gold，以及冻结语料检索结果的标题、日期和最多 500 字符片段；不会发送 dev/test Gold、完整语料、`.env` 或 API 密钥。pilot 通过后再扩到 90 条 train，并运行编译器。
