# Search Agent 轨迹与 SFT/DPO 种子数据

## 1. 本轮目标

按照原计划进入 P2 Search Agent 数据阶段，将已经完成的 Crisis 四主题 exploration 实验转化为可审计、可复现的训练数据格式：

1. 完整保存 `state → action → observation → updated_state`；
2. 从第 2、3 轮决策状态构造 SFT 样本；
3. 在相同状态下回放多个候选查询；
4. 根据可验证的日期覆盖收益构造 DPO chosen/rejected；
5. 固定输入快照、索引哈希、源产物哈希和输出哈希。

本轮完全使用本地冻结语料和 SQLite BM25 索引，没有调用 DeepSeek，也没有消耗 LLM 余额。

## 2. 为什么先生成种子数据

当前只有 Egypt、Libya、Syria、Yemen 四个 Crisis 主题。如果立即训练模型，很容易把少量主题和特定事件记住，无法证明 Search Agent 能泛化。因此本轮重点是验证：

- 状态格式能否完整表达检索进度；
- 候选动作能否在相同状态下公平回放；
- 奖励函数能否产生明确偏好；
- SFT/DPO JSONL 是否可以稳定重建；
- gold 日期是否只用于离线标注，而不泄漏到推理提示词。

产物被明确命名为 `seed_v1`，不能直接当作论文最终训练集。

## 3. 新增代码与配置

### `src/chronos_repro/training_data.py`

提供训练数据构造的纯函数：

- `result_dates()`：从检索结果提取发布日期；
- `covered_gold()`：计算精确或窗口日期覆盖集合；
- `score_candidate()`：计算候选查询的增量收益；
- `rank_candidates()`：按奖励降序、查询文本升序稳定排序；
- `prompt_messages()`：生成对话式 Search Agent 状态提示。

### `scripts/build_search_agent_training_data.py`

完成端到端本地构造：

- 读取四主题 exploration 与 Direct/Rewrite/CHRONOS 基线；
- 重建每一轮之前的已见文档、已覆盖日期、历史查询和近期证据；
- 保存全部 12 条实际行为转移；
- 对第 2、3 轮状态回放候选查询；
- 选择奖励最高查询作为 SFT/DPO chosen；
- 选择奖励最低查询作为 DPO rejected；
- 写入数据集 manifest、快照信息和 SHA-256。

### `configs/search_agent_training_data_v1.json`

固定以下实验条件：

- Crisis 快照：`ece08f344cc94933`；
- 主题：Egypt、Libya、Syria、Yemen；
- 检索 `top_k=20`；
- 只从第 2 轮开始生成训练标签；
- 候选来源：Direct、Rewrite、free CHRONOS 和截至当前轮的 exploration 查询；
- 奖励权重与输出文件名称。

## 4. 状态、动作和观察格式

每条 transition 包含：

- `state.topic`：主题；
- `state.round`：当前轮数；
- `state.corpus_date_bounds`：闭域语料日期范围；
- `state.covered_dates`：动作执行前已覆盖的发布日期；
- `state.date_gaps`：当前日期空白区间；
- `state.previous_queries`：此前已经执行的查询；
- `state.recent_evidence`：最近证据的日期与标题；
- `action`：实际执行的 SEARCH 查询；
- `observation`：返回文档 ID、新文档 ID和新增日期；
- `updated_state`：执行后覆盖日期；
- `metrics`：新文档、新日期、gold 日期增益、查询新颖度与奖励。

推理 prompt 中没有放入 gold 日期。gold 只在离线候选回放后计算标签，避免把答案直接暴露给 Search Agent。

## 5. 候选查询与奖励

对于同一个状态，候选查询来自：

- Direct 初始查询；
- Rewrite 查询；
- free CHRONOS 的逐轮查询；
- 当前 exploration 轨迹中截至本轮已经产生的查询。

每个候选都重新在同一个冻结 BM25 索引上检索 20 篇文档，然后相对于同一个 `covered_dates` 和 `seen_ids` 计算增量收益。

奖励为：

```text
1000 × exact_gold_gain
+ 100 × window_2d_gold_gain
+ 2 × new_date_count
+ query_novelty
```

权重采用分层思想：精确参考日期收益优先，其次是 ±2 天窗口收益，再考虑日期多样性和避免重复查询。当前公式用于种子验证，后续需要消融实验，不能直接宣称是最优奖励。

## 6. 生成结果

| 数据类型 | 数量 | Egypt | Libya | Syria | Yemen |
|---|---:|---:|---:|---:|---:|
| 完整 transitions | 12 | 3 | 3 | 3 | 3 |
| SFT seed | 8 | 2 | 2 | 2 | 2 |
| DPO seed | 8 | 2 | 2 | 2 | 2 |

完整 transition 包含第 1～3 轮；SFT 和 DPO 只使用需要规划下一查询的第 2、3 轮。

DPO 奖励差检查：

| 指标 | 数值 |
|---|---:|
| 最小 chosen-rejected 奖励差 | 1,522.769 |
| 最大 chosen-rejected 奖励差 | 9,337.000 |
| 平均奖励差 | 4,940.100 |
| chosen 奖励严格更高 | 8/8 |

实际 exploration 动作只有 **2/8** 次与离线回放最优查询相同。这说明 SFT 标签不是机械复制原有模型输出，而是用闭域检索结果和 gold 日期覆盖重新选择了更优动作。

## 7. 输出文件作用

目录：`chronos_repro/artifacts/training_data/`

- `crisis_search_agent_transitions_v1.jsonl`：12 条实际轨迹转移，用于行为分析和轨迹模型；
- `crisis_search_agent_sft_seed_v1.jsonl`：8 条对话式 SFT 样本，assistant 输出离线回放最优查询；
- `crisis_search_agent_dpo_seed_v1.jsonl`：8 条对话式 prompt/chosen/rejected 偏好样本；
- `crisis_search_agent_training_manifest_v1.json`：数据量、快照、索引哈希、源产物哈希、输出哈希、奖励公式和限制。

## 8. 复现命令

```powershell
$env:PYTHONPATH = "src"
python scripts\build_search_agent_training_data.py `
  --data snapshots\crisis\ece08f344cc94933 `
  --index artifacts\crisis_ece08f344cc94933.sqlite3 `
  --artifacts artifacts `
  --output-dir artifacts\training_data `
  --top-k 20
```

## 9. 自动化测试

新增 `tests/test_training_data.py`，覆盖：

1. 已见文档不计为新增收益；
2. 精确 gold 日期和 ±2 天日期收益计算；
3. 奖励优先排序与文本平局的确定性；
4. 对话式状态 prompt 序列化。

执行全套测试结果：

```text
22 passed in 7.30s
```

## 10. 当前限制

- 只有 4 个主题、8 个决策状态，规模远不足以正式训练；
- 奖励基于文章发布日期，不是事件抽取后的最终 TLS Date-F1；
- 候选查询来自现有基线，候选空间仍然较窄；
- 当前只生成 seed，没有进行 train/dev/test 主题级划分；
- chosen/rejected 的高奖励差不等于语言质量一定更好；
- gold 日期监督适合训练和离线标注，但评测必须使用完全隔离主题。

## 11. 下一步

下一阶段建议按以下顺序推进：

1. 在 T17 与 Entities 冻结索引上生成更多状态和候选动作；
2. 严格按主题划分 train/dev/test，防止同主题时间信息泄漏；
3. 从文章正文抽取事件日期，区分文章发布日期与事件发生日期；
4. 对奖励权重及 chosen/rejected 构造做消融；
5. 数据量扩大并通过质量审计后，再进行小规模 LoRA SFT；
6. 在 SFT checkpoint 上进行局部 DPO，而不是直接在基础模型上使用当前 8 条偏好对。
