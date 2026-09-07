# Search Agent 主题级 Train/Dev/Test 数据拆分

## 1. 本轮目的

对 Crisis Search Agent seed 数据进行严格的主题级拆分，验证后续 SFT/DPO 训练流水线不会把同一主题同时用于训练、模型选择和最终测试。

本轮只进行本地 JSONL 拆分与验证，没有调用 LLM，也没有消耗 DeepSeek 余额。

## 2. 固定拆分

| Split | 主题 | 用途 |
|---|---|---|
| train | Egypt、Libya | SFT/DPO 优化输入 |
| dev | Syria | 奖励调整、超参数和 checkpoint 选择 |
| test | Yemen | 最终流水线评测，禁止参与优化与模型选择 |

“主题交集为 0”表示三个集合两两没有重复：

```text
train ∩ dev  = ∅
train ∩ test = ∅
dev ∩ test   = ∅
```

这不表示数据集中没有主题，也不表示样本被删除。Egypt、Libya、Syria、Yemen 四个主题均被完整分配，只是每个主题只能属于一个 split。

## 3. 为什么必须按主题隔离

同一个 TLS 主题内部通常共享：

- 事件名称和参与者；
- 关键日期；
- 查询词与实体；
- 参考时间线；
- 高相关性新闻文档；
- exploration 状态和候选查询。

如果按单条样本随机拆分，同一主题的第 2 轮可能进入 train，第 3 轮进入 test。模型已经在训练中见过该主题的关键事件和日期，测试结果会被高估。

主题级拆分要求一个主题的所有 transition、SFT 和 DPO 样本始终在同一 split 中，可以更接近“对未见主题生成搜索动作”的实际泛化要求。

## 4. 新增实现

### `src/chronos_repro/splitting.py`

- 校验 train/dev/test 均存在且非空；
- 检查主题不能出现在两个 split；
- 从 transition 顶层或 SFT/DPO metadata 读取主题；
- 拒绝没有分配的主题；
- 按主题稳定拆分数据行。

### `scripts/split_search_agent_training_data.py`

- 读取 transitions、SFT、DPO 三类 seed JSONL；
- 校验 DPO chosen 和 rejected 不同；
- 校验 chosen 奖励严格高于 rejected；
- 将三类文件分别写入 train/dev/test 目录；
- 输出源文件与结果文件 SHA-256；
- 在 manifest 中记录主题交集、行分配和奖励顺序检查。

### `configs/search_agent_topic_split_v1.json`

固定主题分配、输入文件和用途说明，避免每次运行随机产生不同测试主题。

## 5. 样本数量

| Split | Transitions | SFT | DPO |
|---|---:|---:|---:|
| train | 6 | 4 | 4 |
| dev | 3 | 2 | 2 |
| test | 3 | 2 | 2 |
| 合计 | 12 | 8 | 8 |

全部原始样本都被分配，合计数量与拆分前一致。

## 6. 输出目录

```text
chronos_repro/artifacts/training_splits/
├── train/
│   ├── crisis_search_agent_transitions_v1.jsonl
│   ├── crisis_search_agent_sft_seed_v1.jsonl
│   └── crisis_search_agent_dpo_seed_v1.jsonl
├── dev/
│   ├── crisis_search_agent_transitions_v1.jsonl
│   ├── crisis_search_agent_sft_seed_v1.jsonl
│   └── crisis_search_agent_dpo_seed_v1.jsonl
├── test/
│   ├── crisis_search_agent_transitions_v1.jsonl
│   ├── crisis_search_agent_sft_seed_v1.jsonl
│   └── crisis_search_agent_dpo_seed_v1.jsonl
└── crisis_search_agent_split_manifest_v1.json
```

## 7. 复现命令

```powershell
$env:PYTHONPATH = "src"
python scripts\split_search_agent_training_data.py `
  --input-dir artifacts\training_data `
  --output-dir artifacts\training_splits `
  --config configs\search_agent_topic_split_v1.json
```

## 8. 验证结果

Manifest 检查：

```text
topic_overlap = false
all_rows_assigned = true
dpo_reward_order_valid = true
```

连续重建后全部拆分文件 SHA-256 保持一致：

```text
deterministic = true
```

自动化测试结果：

```text
25 passed in 4.53s
```

新增测试覆盖：

- 主题级分组；
- train/dev/test 主题重复检测；
- 未分配主题检测；
- transition 与 SFT/DPO 两种主题字段格式。

## 9. 当前限制

当前拆分的主要作用是验证数据隔离和训练代码接口，不能用于论文统计结论：

- train 只有两个主题；
- dev 和 test 各只有一个主题；
- test 指标会对 Yemen 单个主题特征高度敏感；
- 当前标签仍基于文章发布日期覆盖，而非完整的事件日期与内容质量。

扩大到 T17 和 Entities 后，应重新制定主题级拆分，并保持测试主题在查询生成、奖励调参、SFT 和 DPO 全流程中完全隔离。
