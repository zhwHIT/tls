# Crisis 当前流程重跑：原始关键词

用户要求：使用现在的流程和prompt重跑Crisis，并回退关键词，不加入crisis。

- 主题：egypt、libya、syria、yemen；有效关键词分别为 `["egypt"]`、`["libya"]`、`["syria"]`、`["yemen"]`。
- 四个主题从空时间线开始，只运行第一阶段。
- 模型 deepseek-v4-flash，temperature=0；每主题最多24批，每批最多3条query，每条top-12，3个主题并发。
- 检索文章原文，返回预提取的事件与时间；VERIFY判断相关性、纳入和重复关系。
- 已接受的部分日期事件不再生成待补齐队列。SEARCH不接收待补齐事件、reason、尝试记录或target_lead_ids。
- SEARCH不接收batch_feedback、连续零变化、每query的批次变化数、收益归因或retrieval_feedback.event_changes。内部日志仍可记录统计。
- SEARCH根据已有事件追踪后续发展、参与者反应、决策执行及影响，包含人物、国家地区、机构企业和事件危机类主题的提示。
- 当前英文system prompt保存于system_prompt.txt。
- 关闭代理；网络中断暂停，恢复后继续。已有82项相关测试通过。
- 17,573条文章映射预检通过；Gold仅在运行结束后用于日期覆盖率评估。

含crisis关键词、刚启动即被用户纠正的运行位于 `artifacts/preextracted_crisis_developments_20260922`。该轮已设置STOP并于2026-09-22 13:40:46 UTC停止，三个主题各1次SEARCH调用，未进行VERIFY。日志和请求账本保留，该轮不会续跑，也不计入本次结果。

本次结果与原始 `artifacts/preextracted_all_phase1_20260920` 对比，不使用keyword修改实验作为对照。有效关键词、最大预算与原始运行相同；原始Syria在格式恢复修复后续跑，其他原始主题早于修复，因此不是只有prompt不同的严格单变量实验。实际停止位置和API用量也可能不同。

启动或续跑：

```powershell
D:/miniforge/envs/tls/python.exe -u scripts/run_preextracted_all_suite.py --suite-config configs/preextracted_crisis_developments_topic_only_20260922_suite.json --output-root artifacts/preextracted_crisis_developments_topic_only_20260922 --workers 3 --allow-api
```

运行状态：suite_status.json；汇总：summary.json；每主题日志：crisis_TOPIC/run.log。

结束后执行本目录compare.py生成comparison.json与comparison.md。包含各主题Gold日期覆盖率、实际批次、查询数、停止原因、新增及丢失的命中日期。

## 完成结果

于2026-09-22 13:54:20 UTC完成。四主题均运行成功，均达到24批执行上限，非模型自主判定覆盖充分。累计505次请求、1,248,829返回tokens，不含被停止的前一轮3次SEARCH调用。

| 主题 | 原始命中 | 本次命中 | 本次覆盖率 | 本次query数 |
|---|---:|---:|---:|---:|
| Egypt | 34/122 | 40/122 | 32.79% | 69 |
| Libya | 51/118 | 55/118 | 46.61% | 60 |
| Syria | 27/106 | 25/106 | 23.58% | 65 |
| Yemen | 31/81 | 36/81 | 44.44% | 66 |

宏平均33.71%→36.86%，提高3.15个百分点；微平均143/427→156/427，即33.49%→36.53%。

96个在线SEARCH输入已检查：关键词均只有主题名，均不含待补齐或收益反馈字段。各主题待补队列均为空；年/月/区间/未知精度事件仍正常纳入，共分别5、2、3、11条。见live_prompt_audit.json。

Syria检索命中26个Gold日期，最终保留25个。2012-01-24对应摘要“Syria rebukes the Arab League.”因信息过少被VERIFY忽略；其他三主题检索命中的Gold日期均进入最终时间线。见selection_gap_audit.json。

所有原始基线gold_date_coverage.json与启动前保存的baseline.json一致。最终逐日期对比已写入comparison.json。
