# Crisis 四主题查询策略实验进度

## 1. 本轮目标

在冻结的 Crisis 语料快照上，将 Egypt 的查询策略实验扩展到 Libya、Syria、Yemen，并比较以下四类检索方式：

1. `direct`：直接使用主题查询；
2. `rewrite`：由 LLM 改写查询；
3. `free_chronos`：由 LLM 一次性生成多条时间线导向查询；
4. `exploration_chronos`：先检索，再根据已覆盖日期和空白区间生成下一轮查询。

本轮仍然只评估“检索到的文章发布日期对参考时间线日期的覆盖情况”。它是 Search Agent 检索层的诊断指标，不是最终 TLS 摘要质量指标，也不能替代 Date-F1、ROUGE 或语义匹配评测。

## 2. 数据授权与隐私边界

用户已明确授权将 Crisis Libya、Syria、Yemen 检索结果中的标题、日期和摘要片段发送给 DeepSeek，以便执行查询规划。调用配置从 `chronos_repro/.env` 读取，密钥不写入实验产物、日志或本文档。

`.env` 已由仓库本地排除规则忽略，提交前继续使用 `git check-ignore` 和暂存区检查确认其不会进入 Git。

## 3. 执行过程与动机

### 3.1 基础查询策略

对 Libya、Syria、Yemen 分别执行 `direct`、`rewrite`、`free_chronos`。这样可以先建立每个主题的固定查询基线，再判断多查询是否扩大日期覆盖。

### 3.2 Exploration-aware CHRONOS

对每个主题执行最多 3 轮的探索式检索：

- 第 1 轮以主题时间线查询启动；
- 后续轮次把已覆盖日期、日期空白区间和上一轮结果反馈给 LLM；
- 查询相似度过高或连续无新增日期时停止；
- 每轮保留查询动作、检索观察和覆盖状态，便于后续构造 SFT/DPO 轨迹。

Libya 和 Syria 完成全部流程。Yemen 的基础策略已完成，但 exploration 在第一次 LLM 请求时遇到 TLS/SSL 连接被远端提前关闭。程序按既定策略立即停止，没有自动重试，也没有产生 token 消耗。

## 4. 产物与文件作用

- `chronos_repro/artifacts/libya_query_baselines_deepseek_v1.json`：Libya 三种基础策略、检索结果与调用用量；
- `chronos_repro/artifacts/libya_exploration_chronos_v1.json`：Libya 多轮探索轨迹；
- `chronos_repro/artifacts/syria_query_baselines_deepseek_v1.json`：Syria 三种基础策略结果；
- `chronos_repro/artifacts/syria_exploration_chronos_v1.json`：Syria 多轮探索轨迹；
- `chronos_repro/artifacts/yemen_query_baselines_deepseek_v1.json`：Yemen 三种基础策略结果；
- `chronos_repro/artifacts/yemen_exploration_chronos_v1.json`：Yemen exploration 的结构化失败记录，包含状态与错误类型，不包含密钥；
- `chronos_repro/scripts/aggregate_crisis_retrieval.py`：统一读取四主题参考时间线和实验产物，计算发布日期的精确日期召回与 ±2 天召回；
- `chronos_repro/artifacts/crisis_query_strategy_aggregate_v1.json`：逐主题、可用结果宏平均、三主题严格配对宏平均及失败信息。

## 5. 逐主题结果

下表的数值均为发布日期覆盖召回率。

| 主题 | 策略 | 文档数 | 唯一日期数 | 精确日期召回 | ±2 天召回 |
|---|---|---:|---:|---:|---:|
| Egypt | direct | 20 | 16 | 8.20% | 19.67% |
| Egypt | rewrite | 20 | 16 | 8.20% | 19.67% |
| Egypt | free_chronos | 57 | 17 | 6.56% | 6.56% |
| Egypt | exploration_chronos | 41 | 32 | 12.30% | 25.41% |
| Libya | direct | 20 | 14 | 5.08% | 18.64% |
| Libya | rewrite | 20 | 14 | 5.08% | 18.64% |
| Libya | free_chronos | 59 | 45 | 10.17% | 34.75% |
| Libya | exploration_chronos | 50 | 42 | 7.63% | 25.42% |
| Syria | direct | 20 | 18 | 1.89% | 10.38% |
| Syria | rewrite | 20 | 18 | 2.83% | 11.32% |
| Syria | free_chronos | 56 | 49 | 6.60% | 24.53% |
| Syria | exploration_chronos | 60 | 48 | 4.72% | 17.92% |
| Yemen | direct | 20 | 19 | 8.64% | 29.63% |
| Yemen | rewrite | 20 | 19 | 8.64% | 29.63% |
| Yemen | free_chronos | 39 | 24 | 3.70% | 22.22% |
| Yemen | exploration_chronos | — | — | — | — |

Yemen exploration 的缺失值没有按 0 分处理，因为它是调用失败，不是策略完成后的实验结果。

## 6. 聚合比较

### 6.1 四主题可用结果

基础策略在四个主题上均完成：

| 策略 | 主题数 | 平均唯一日期数 | 宏平均精确召回 | 宏平均 ±2 天召回 |
|---|---:|---:|---:|---:|
| direct | 4 | 16.75 | 5.95% | 19.58% |
| rewrite | 4 | 16.75 | 6.19% | 19.82% |
| free_chronos | 4 | 33.75 | 6.76% | 22.01% |

`free_chronos` 显著增加了检索日期的多样性，但不同主题上的收益不稳定；例如 Libya、Syria 有提升，Egypt、Yemen 反而下降。因此，单纯增加 LLM 查询数量并不等于稳定提高时间线日期覆盖。

### 6.2 三主题严格配对比较

为公平比较四种策略，只在 Egypt、Libya、Syria 三个全部完成的主题上计算配对宏平均：

| 策略 | 平均文档数 | 平均唯一日期数 | 宏平均精确召回 | 宏平均 ±2 天召回 |
|---|---:|---:|---:|---:|
| direct | 20.00 | 16.00 | 5.06% | 16.23% |
| rewrite | 20.00 | 16.00 | 5.37% | 16.55% |
| free_chronos | 57.33 | 37.00 | 7.78% | 21.94% |
| exploration_chronos | 50.33 | 40.67 | 8.21% | 22.92% |

在三主题严格配对结果中，`exploration_chronos` 的日期多样性、精确召回和 ±2 天召回均最高，但相对 `free_chronos` 的提升较小，而且 Libya、Syria 的单主题结果并未超过 `free_chronos`。当前证据支持“反馈式探索具有潜力”，但还不足以说明其稳定优于一次性多查询。

## 7. LLM 用量与停止情况

| 产物 | 状态 | total tokens |
|---|---|---:|
| Egypt 基础策略 | 成功 | 5,129 |
| Egypt exploration | 成功 | 2,583 |
| Libya 基础策略 | 成功 | 7,789 |
| Libya exploration | 成功 | 3,342 |
| Syria 基础策略 | 成功 | 6,227 |
| Syria exploration | 成功 | 2,609 |
| Yemen 基础策略 | 成功 | 7,348 |
| Yemen exploration | `stopped_llm_error` | 0 |
| 合计 | — | 35,027 |

Yemen 报错为：`SSL: UNEXPECTED_EOF_WHILE_READING`。这表示 HTTPS 连接在 TLS 数据读取阶段意外结束，不是 API 返回的余额不足。由于本轮采用“LLM 调用失败即停止、不自动重试”的保护策略，因此没有继续发送请求。

## 8. 验证结果

运行：

```powershell
conda run -n tls python -m pytest -q chronos_repro\tests
```

结果：`16 passed in 8.66s`。

聚合脚本成功生成 JSON；它明确区分：

- 四主题均可用的基础策略宏平均；
- 三主题四策略严格配对宏平均；
- Yemen exploration 的失败状态。

## 9. 当前结论与下一步

目前已得到可复现的多主题查询规划轨迹。下一步应优先在不调用 LLM 的前提下：

1. 分析每轮查询的重复度、日期增益和边际收益；
2. 从轨迹构造 SFT 样本：`状态 -> 下一条查询`；
3. 构造 DPO 偏好对：优先选择带来更多新日期、更多参考日期覆盖且重复度更低的查询；
4. 将发布日期覆盖诊断升级为事件日期抽取后的 Date-F1，并补充内容质量评测；
5. Yemen exploration 保持失败记录，只有在用户明确要求重试时再恢复调用。
