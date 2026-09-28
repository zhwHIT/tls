# SEARCH 早期职业生涯追查

2026-09-18，用户要求完善“允许 SEARCH 向时间线起点之前补查职业生涯早期事件”。本次只修改本地策略并验证，没有调用 API、恢复停止任务或修改历史事件。

## 事实核对

原执行器没有把当前最早事件日期自动设为检索下界：`batched_search_phase.execute_batch` 在普通第一阶段直接使用模型输出的 `time_filter`。上一轮 30 条 query 的时间过滤都是 `none`。此前答复中“1996 年以前被时间窗口排除”的归因不成立。

当前确认时间线 1996-09-01 至 2011-11-20 **没有覆盖** Gold 的 1995-04-02 至 2018-01-29 起止。预测区间位于 Gold 区间内，不等于预测区间覆盖 Gold 区间；此前相关结论方向写反。

1995-04-02 的证据存在于冻结语料文档 20843，原文明确写 league debut on 2 April 1995。本次停止运行没有检索到该文档，也没有生成专门针对曼联英超首秀的查询。不能据此断言是日期过滤所致。

## 本次实现

`src/chronos_repro/batch_memory.py` 增加 `chronology_frontier`，仅用于无显式事件区间限制的第一阶段：

- 从当前事件集合计算 `earliest_known_event`，包含 event ID、日期和摘要；不依赖压缩摘要是否保留了这条事件。
- 显式传递 `earliest_is_search_lower_bound=false`，当前已知事件不能证明更早经历完整。
- UPDATE、DELETE 后下一次读取重新计算；冲突事件和不完整日期不作为这个日期锚点；空时间线返回空锚点。
- 不向第二阶段或具有 `event_scope` 的区间任务添加扩展提示。

`src/chronos_repro/batch_policy.py` 强化人物检索提示：

- 主动考虑当前最早事件之前的职业生涯，而不是持续围绕已知的后期线索补日期。
- 利用已知职业、机构拆分首次签约/任命、职业首秀、俱乐部/联赛首秀、国家队首秀；找到一种首秀不代表其他首秀已经覆盖。
- 可以在没有 pending lead 时使用 `target_lead_ids=[]` 发起新方向查询。
- 通过查询文字限定事件，使用 `time_filter.mode=none` 保留后来发表的回顾文章；不能用事件日期裁剪文章发表日期。
- STOP 前考虑是否仍有值得追查的早期职业问题；一次无结果不证明完整。

这是策略提示与可见状态增强，没有固定插入查询、强制最低批数或每批预留 query。实际查询仍由 SEARCH 决定，是否提升 Gold 日期覆盖率需要另行在线评测。本次未把 Gold 日期或文档 20843 注入检索策略。

## 验证

- `test_batch_controller.py`、`test_joint_evidence_feedback.py`、`test_phase1_supplement.py`：**64 passed**。
- 新回归检查覆盖摘要丢失早期事件、UPDATE/DELETE、冲突和部分日期、区间边界保留，以及空 lead 新方向查询无隐含日期裁剪。模拟回顾文章通过执行器，但不伪装成真实检索收益。
- `analysis_tools/check_early_career_policy.py` 离线检查 10 个历史 SEARCH 输入：一条未经缩页为 3986 tokens，按现有缩页机制暂时少展示 2 条 lead 后为 3765；其他输入在上限内。没有截断 reason 或删除队列项。
- 当前停止状态的新策略视图为 3722 tokens，8 条可见 lead，预检上限 3800。
- 该预算检查使用最终起点向历史输入加压，不是时间顺序策略重放，不生成新的 query、模型输出或 Gold 指标。
- 原停止检查点 SHA-256 未变化。预算报告：`chronos_repro/artifacts/early_career_policy_offline/report.json`。
