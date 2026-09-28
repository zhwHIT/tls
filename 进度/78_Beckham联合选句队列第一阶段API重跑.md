# Beckham 联合选句与队列：第一阶段 API 重跑

2026-09-18，用户明确授权按当前检索策略调用 API，重新运行 David Beckham 第一阶段并查看 Gold 时间覆盖率。

## 范围与主指标

从空事件库开始，使用当前混合检索（BM25＋Dense、top-12）和“原相关句＋主事件候选句＋日期提及句”的联合选择，保留未读候选。模型为 `deepseek-v4-flash`。控制器根据新证据自主生成后续 query，不固定旧 51 条 query；这是新的完整第一阶段诊断运行，不是固定检索结果的消融。

仅运行 `SKELETON_EXPLORATION`，第一阶段结束后保存事件库并评测，不执行补充阶段、第二阶段或全流程 FINAL_SELECT。主指标为最终事件日期对全部 **16 个 Gold 日期**的精确日期召回，Gold 只供运行后的评测使用。

## 在线接入与预算

- 在线选择器：[sentence_reader.py](../chronos_repro/src/chronos_repro/sentence_reader.py)。与离线实验相同的三路评分、45%/75%/100% 累积预算、候选等待加分及词面重复惩罚。
- 每个新 query 先用旧段落选择器计算当前查询的参考证据，再以统一的来源及偏移格式计费，作为新选择的 token 和正文字符上限。旧段落仅在其完整可见原文区间已经展示时退出参考候选；不使用历史轨迹的未来结果。
- 不连续句子输出为不同的连续片段，保留源偏移和正文哈希；选中的前文已包含在预算内，不再免费附加 400 字符。冻结日期注释仅绑定可见源区间。当前 VERIFY 按原有每次一个片段处理，不假设模型能够跨请求看到所有入选句子。
- 未选中源句跨 query 保留；已选源区间在同一批次后续 query 不重复安排。`processed` 仍只在完整抽取成功后标记；不完整抽取另存 checkpoint。
- 最多 24 批、每批 3 条 query；沿用非 VERIFY 请求累计上限 500、VERIFY 单独记账且不限次数。单次输入预检 3800、硬上限 4096 tokens。证据预算相同不代表总 API 费用相同。

## 检查与产物

56 项相关测试通过，涵盖队列保留、超预算整句不截断、原文范围、与离线候选切分一致、仅第一阶段边界及现有覆盖/补充流程回归。真实语料预检通过：主题共 2,945 篇文档，检索返回 12 篇，新选择器选中 10 个片段、来自 4 篇文档；该预检无 API 调用。

- [运行配置](../chronos_repro/configs/beckham_sentence_queue_phase1_api.json)
- 输出目录：`chronos_repro/artifacts/beckham_sentence_queue_phase1_api`
- 实时轨迹：`checkpoint.json`；请求账本：`request_ledger.json`；执行日志：`run.log`
- 最终选择历史与未读候选：`evidence_reader_manifest.json`
- [独立 Gold 日期评测脚本](../chronos_repro/analysis_tools/evaluate_beckham_sentence_phase1.py)

## 运行结果

用户随后要求停止探索，已终止 API 进程。停止时第 12 批尚未完成，检查点保存 24 条事件，实际命中 **1995-04-02**，Gold 日期覆盖率 **1/16 = 6.25%**；这是中止快照，不是完整第一阶段结果。此前沿用较早快照的 0/16 已更正。

共记录 35 条 query、156 篇召回文档、254 次 VERIFY；请求账本累计发起 305 次。已有证据表明待补证线索的哈希排序前四项截断、单句验证及年份混淆是重要阻断点。详细逐 Gold 排查与停止快照见 [79_Beckham停止探索后的Gold遗漏原因排查](79_Beckham停止探索后的Gold遗漏原因排查.md)。后续未继续调用 API。
