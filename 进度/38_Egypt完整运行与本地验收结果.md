# Egypt 完整运行与本地验收结果

本报告由本地收尾脚本生成，不调用模型，不启动新轨迹，不扩大授权范围。

- 程序状态：`stopped_insufficient_balance`。
- 最终事件：113；不同日期：86。
- gold 日期命中：47/122；recall：0.38524590163934425。
- 日期 recall ≥70%：False。
- 两阶段均执行：True；第二阶段自主停止：False。
- 强制 STOP 次数：1；终态 gap memory 可用：False。
- 来源与日期表达式审计通过：True。
- HTTP 累计：327/400；余额停止：True。

## 结论边界

词汇匹配和 Timeline ROUGE 不是语义蕴含评测。即使日期目标通过，也不自动认为事件覆盖、STOP 判断或训练标签质量通过。
当前 training_ready=false；需要语义复核、精确推理 prompt 对齐与独立数据划分后，才能进入训练。

## 产物

运行目录：`D:\paper\chronos_repro\artifacts\tisa_coverage_egypt_v6`。

- `acceptance_report.json`：分项验收结论。
- `evidence_audit.json`：片段来源、正文偏移、日期表达式检查。
- `gold_evidence_layers.private.json`：本地日期分层诊断与 Timeline ROUGE，禁止作为本次策略输入。
- `local_evaluation_commands.json`：本地评测命令输出与退出码。

下一步根据未通过项修复，不自动更换目录重置额度，不自动启动新的付费运行。
