# Egypt 文档 Memory 完整试跑与诊断

> 日期：2026-09-11。
> 本轮已完成：status=ok，83 步、20 次检索、94 个最终事件；流程通过审计，但完整覆盖和自主停止未达标。

## 1. 本轮目标与固定设置

检查文档驱动 memory 是否帮助模型自主发散 query、形成跨时段粗骨架，并检验真实自主停止，而不是只验证单次 query。

- 环境：D:\miniforge\envs\tls\python.exe；本轮未安装新依赖。
- 模型：既有 .env 配置的 deepseek-v4-flash，端点沿用 DeepSeek；不打印密钥。
- 主题：crisis / egypt；冻结 keywords 为 egypt、egyptian。
- 不使用 Egypt 专用 task_description，不向策略发送 Gold。
- 混合检索：原 v3 BM25 + 多语言 Dense，Top-8，每篇最多 700 字符。
- 第一阶段：至少 6 轮后开放自主 STOP，16 轮安全上限，正式事件不少于 4、粗阶段不少于 3，要求 EARLIER/LATER 探索和模型就绪标记。
- 第二阶段：保持最多 4 个 gap 周期与 none/soft/hard 时间检索设置。
- 新目录：chronos_repro/artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1。
- 历史对照：chronos_repro/artifacts/tisa_two_phase_temporal_egypt_v5，不覆盖其原始产物。

## 2. 实际发生的错误及修复

### 2.1 历史阶段出处被错误拒绝

第一次运行在第二轮 MEMORY_UPDATE 后因校验失败而停止，status=stopped_error，并非余额不足。错误为：stage must cite current or remembered document IDs。

只读核查证实，粗阶段引用的文档 2906、4061 都在第一轮真实检索记录里，但没有对应的 observed_events。旧校验集合只包含“当前检索文档 + 已抽取观察的文档”，漏掉了“历史检索过但未单独抽取观察的文档”。

修复为以下集合的并集：

1. 本次检索的文档 ID。
2. 既有 observed_events 中的文档 ID。
3. memory.search_history 中实际返回过的文档 ID。

从未检索过的文档 ID 仍被拒绝。新增回归测试覆盖合法历史出处和伪造出处两种情况，不以删除出处校验规避错误。

第一次失败的 trajectory、prediction、evaluation、manifest 等根目录文件已复制到 attempt_01_before_memory_history_fix。API 缓存保留原样。随后重新进入执行器，重放缓存中的相同请求，再继续新步骤；缓存重放不会重复付费。

### 2.2 VERIFY 的日期精度修复

第一轮 VERIFY 曾返回月份粒度的 2011-01，正式事件接口要求 YYYY-MM-DD，因此进入已有的有限修复流程。这里与 memory 不同：memory 可以保留月份或未知日期，但正式事件需要满足评测接口。日期格式通过并不保证语义归一化正确，最终仍需要证据质量检查。

### 2.3 重试耗尽后的逐条拒绝与继续

补充错误记录：第八轮整批 memory 修复耗尽后，最后一份响应有 9 条合法观察、3 条缺少日期表达或引文不匹配的观察；第九轮又出现无出处的新关键词。相应失败产物分别归档在 attempt_02_before_row_filter、attempt_03_before_keyword_filter。

处理方式不是放宽日期/引文规则：新增重试耗尽后的逐条校验，保留通过原有规则的观察，拒绝错误观察与无出处关键词。拒绝项、原始内容及理由记录在 validation_rejections；原始 API 缓存不改写。thought、粗阶段、搜索方向等全局字段仍需正常校验；如果所有观察都失败，仍停止而不伪造有效标签。

经过过滤的步骤使用 deepseek_actual_rollout_filtered_after_validation 标签来源，与原始未经滤除的模型输出区分。第一次恢复命中 37 次缓存请求，避免重新标注前面已经完成的步骤。完整审计会复验过滤后的 memory 状态变化，不把结构检查等同于语义正确性。

## 3. 闭域语料日期诊断与重要更正

通过 SQLite 只读查询得到 Egypt 索引的文章发布日期元数据统计：

| 项目 | 结果 |
|---|---|
| 文档数量 | 4,083 |
| 最早发布日期 | 2011-01-16 |
| 最晚发布日期 | 2013-07-22 |
| 2011 年文章 | 1,312 |
| 2012 年文章 | 1,049 |
| 2013 年文章 | 1,722 |

模型执行过多个查找 2013 年 8 月之后发展的 query。元数据没有更晚的时间戳，但不能据此断言正文不包含更晚事件：抽查发现聚合页面文档 1532 的发布日期元数据为 2011-02-03，正文却明确含有 27 Jul 2013 的内容，甚至晚于整个集合的元数据上界。运行中的初步“超出语料边界”判断需据此修正为“超出发布日期元数据范围，内容可达性尚不能由此判断”。

该统计保存于新运行目录的 corpus_date_audit.json，未临时加入本轮模型输入。它不是 Gold 标签或人为指定的主题框架；以后最多作为弱元数据提示，不能直接作为硬停止条件或正文内容上界。文章发布日期既不能约束所有历史回顾日期，也可能与后续更新/聚合内容日期不一致。两项针对性证据抽查见 evidence_spotcheck.json。

## 4. 审计方法与新增文件

- scripts/analyze_exploration_rollout.py：离线重放每个 MEMORY_UPDATE 的状态变化，检查阶段二是否继承探索 memory，统计 query 方向、新文档数、正式事件年份分布，并与旧 pilot 做描述性比较。
- tests/test_exploration_audit.py：检验正常记忆更新可重放，且能发现历史查询被改写、阶段二漏传 memory。
- tests/test_exploration_memory.py：新增历史文档出处回归测试。
- scripts/audit_temporal_rollout.py：沿用原有检索重放、日期动作、SFT 与轨迹一致性审计。

最后完整离线测试为 **122 passed**。包括历史出处、逐条观察/关键词拒绝、记忆状态重放以及训练 STOP 筛查；新脚本通过语法编译。

注意：本轮第一阶段与旧 pilot 的搜索轮数不同，因此即便覆盖提高，也不能直接将全部差异归因于 memory。需要后续等轮数、有/无 memory 的对照实验。

## 5. 运行与复验命令

在 D:\paper\chronos_repro 下：

```powershell
conda activate tls
$env:PYTHONPATH='src'
python scripts/run_tisa_two_phase_annotation.py --project-root . --config configs/tisa_two_phase_temporal_v5.json --env-file .env --output-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1

# 以下审计命令应在 trajectory.json 最终落盘后运行；不调用 API。
python scripts/analyze_exploration_rollout.py --run-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1 --baseline-dir artifacts/tisa_two_phase_temporal_egypt_v5 --output artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1/exploration_comparison.json
python scripts/audit_temporal_rollout.py --run-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1 --config artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1/run_config.json --output artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1/audit.json
```

## 6. 最终验收

完整 API 执行已结束，无后台模型请求继续运行。本轮未触发余额不足错误；这不表示查询过账户余额。

### 6.1 与旧 pilot 的描述性比较

| 项目 | 旧 pilot | 本轮 |
|---|---:|---:|
| 第一阶段 SEARCH 次数 | 2 | 16 |
| 第一阶段不同文档数 | 12 | 91 |
| 第一阶段正式事件数 | 5 | 81 |
| 最终事件数 | 19 | 94 |
| 最终不同日期数 | 17 | 64 |
| Gold 日期覆盖 | 12/122（9.84%） | 30/122（24.59%） |
| Date precision | 70.59% | 46.88% |
| Date F1 | 17.27% | 32.26% |
| 当前文本匹配代理匹配的 Gold 事件 | 1/201（0.50%） | 6/201（2.99%） |
| 最后开放 gap | 2 | 7 |

结论：覆盖广度扩大，但精确率下降，事件匹配仍很低。当前事件匹配是限定日期上的文本相似度代理，不等于人工语义召回。新旧实验同时改变了轮数、memory、提示与状态传递，因此不能据此宣称 memory 的独立因果收益。

第一阶段 81 个事件中，2011 年 12 个、2012 年 20 个、2013 年 49 个，分布仍偏向 2013 年。探索 memory 累积 157 条暂定观察，其中 104 条没有规范化事件日期；不能把这些观察全部当作有确切日期的已验证事件。

### 6.2 检索发散与停止表现

第一阶段实际 query 与新增文档数：

| 轮次 | 方向 | 新文档 | query |
|---|---|---:|---|
| 1 | DISCOVER | 8 | Egypt Egyptian crisis key events timeline |
| 2 | LATER | 8 | Egypt July 2013 Morsi ousted military intervention aftermath |
| 3 | EARLIER | 8 | Egypt Mubarak resignation February 2011 military takeover transition |
| 4 | LATER | 6 | Egypt after July 2013 Morsi crackdown later transition |
| 5 | INTERVAL | 6 | Egypt 2011 parliamentary elections Mubarak trial transition early 2012 |
| 6 | LATER | 8 | Egypt after July 2013 Morsi crackdown August Rabaa trials |
| 7 | LATER | 5 | Egypt August 2013 Rabaa crackdown state of emergency Morsi |
| 8 | INTERVAL | 7 | Egypt March October 2011 military transition protests referendum |
| 9 | INTERVAL | 3 | Egypt June 2012 Morsi inauguration early presidency transition |
| 10 | LATER | 6 | Egypt after August 2013 Morsi trial Sisi presidency |
| 11 | EARLIER | 8 | Egypt before 2011 Mubarak regime background unrest |
| 12 | LATER | 0 | Egypt Rabaa dispersal August 2013 Morsi trial 2014 |
| 13 | LATER | 6 | Egypt Sisi presidency 2014 elections Morsi trial |
| 14 | LATER | 3 | Egypt Rabaa dispersal August 2013 state of emergency Morsi trial Sisi |
| 15 | LATER | 6 | Rabaa al-Adaweya dispersal August 14 2013 |
| 16 | LATER | 3 | Rabaa al-Adaweya dispersal August 14 2013 Egypt state emergency |

可以看到早期、中间和后续检索确实发生，但后半段反复追查相近的 Rabaa/2014 方向，第 12 轮没有新文档。不同 query 不等于不同意图，新文档也不等于新事件。第一阶段最终 skeleton_ready=false，phase1_termination=runner_limit；第 65 步是程序安全上限 STOP，不是自主完成。

第二阶段 4 次搜索依次针对 gap-001、gap-008、gap-008、gap-008。后者因优先级较高持续被选择，其他 6 个开放 gap 没有得到查询机会。需要改进 gap 调度，防止某一方向长期占用后续轮次。

四次日期动作分别为 soft、soft、none、none：模型在有界检索未获得所需后续证据后取消日期约束，这个回退分支已实际执行；本轮未使用 hard，不能宣称本轮检验了 hard 的实际效果。

### 6.3 离线评测与重放

- 20/20 次检索结果 ID 及顺序重放一致，共命中 115 篇不同文档。
- 16/16 次探索 memory 更新可重放，阶段二确实继承最终探索 memory。
- 日期动作通过结构与可见锚点校验，最终事件的证据 ID 均来自检索记录。
- 这些检查不证明日期归一化、事实摘要或 gap 已解决判断的语义正确性。

使用项目已安装的 Tilse `reimpl` 后端做新旧同口径比较（可移植近似实现，不是原始 Perl ROUGE）：

| F1 指标 | 旧 pilot | 本轮 |
|---|---:|---:|
| Concat ROUGE-1 | 0.20384 | 0.31034 |
| Concat ROUGE-2 | 0.03265 | 0.05023 |
| Date+content aligned ROUGE-1 | 0.03560 | 0.04029 |
| Date+content aligned ROUGE-2 | 0.00874 | 0.00969 |

长文本拼接覆盖增幅大于日期对齐指标增幅；与时间归一化和事件精确匹配仍存在问题相一致，但这不是对单一原因的因果证明。

### 6.4 证据抽查发现

1. event-001 被写为 2011-01-01，但文档 563 片段只有 January 2011。月份细化为 1 日缺少原文支持。该错误还影响了第二阶段 gap-001 的时间锚点。原始 prediction 保留，不在评测后偷偷改答案。
2. event-055 的 2013-07-27 在文档 1532 正文中有明确日期支持，虽然其发布日期元数据是 2011-02-03。这是元数据与聚合内容不一致的例子，不能仅因超过元数据上界就判为模型臆造。

这里只抽查两项，不是全量事实审核。优先修复正式事件的日期精度表示与证据日期对齐，而不是把所有月份强制变成某个日。

### 6.5 训练产物与调用用量

- 原始轨迹：83 步；原始 sft_v5.jsonl：82 条（第一阶段强制 STOP 已排除）。
- 第二阶段第 83 步 STOP 时仍有 7 个 OPEN gap，因此从另存的结构筛查集里排除。
- sft_v5_structurally_filtered.jsonl：81 条；其中 3 个 memory 步骤带校验过滤标记。
- 合计拒绝 4 条观察、1 个无出处关键词；原始响应不改写。
- training_filter_report.json 明确 training_ready=false，所有保留样本仍需语义审核。

本轮目录共保留 **104 个成功 API 响应**，累计 API 返回的用量为 **2,671,090 tokens**：prompt 2,080,197，completion 590,893。这个总数包含失败尝试中已返回并计量的响应，不只统计最后一次进程。

最后一次进程为 43 次缓存命中、60 个新响应；不能用 new_calls_this_run=60 代表整个实验。与旧 pilot 的 281,775 tokens 相比，用量约 9.48 倍，而日期对齐质量提升较小，因此不应直接扩大量产。以上不是账户账单或余额核验。

## 7. 新产物用途与下一步优先级

新运行目录主要文件：

- trajectory.json / prediction.json / evaluation.json：原始完整轨迹、时间线与基础评测。
- run_config.json / manifest.json / api_cache/：运行配置快照、追溯信息及原始响应缓存。
- exploration_comparison.json：第一阶段发散统计、年份分布、状态重放和旧 pilot 比较。
- audit.json：全部检索重放及协议一致性结果。
- tilse_reimpl_current.json / tilse_reimpl_baseline.json：相同后端的 Timeline-ROUGE 结果。
- corpus_date_audit.json / evidence_spotcheck.json：元数据日期统计与两个针对性证据问题。
- sft_v5.jsonl / sft_v5_structurally_filtered.jsonl / training_filter_report.json：原始样本、保守结构筛查集和未通过质量验收的声明。
- attempt_01_before_memory_history_fix/、attempt_02_before_row_filter/、attempt_03_before_keyword_filter/：失败阶段产物，保留问题定位链。

建议下一步按以下顺序推进，不先扩大 API 标注量：

1. 日期精度：区分日、月、不确定时间，禁止无证据补成月初；对聚合页元数据设置可信度，不把 publication_date 当作事件日期或硬停止边界。
2. 探索收敛：记录同一检索意图的重复与已验证事件增益，而不只看 query 是否不同、文档是否新增；允许把暂不可证实的方向挂起。
3. Gap 调度：给反复无有效增益的 gap 降优先级或冷却，让其他 OPEN gap 获得处理机会。
4. 成本与状态：避免所有 VERIFY/MERGE 调用反复携带完整累计观察；保留任务所需证据和记忆摘要，做受控对照。
5. 在这些修复后，再以同轮数、同语料比较有/无 memory，做事件/日期人工抽查，通过后扩展 SFT/DPO 数据。
