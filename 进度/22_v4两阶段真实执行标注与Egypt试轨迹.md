# v4 两阶段真实执行标注与 Egypt 试轨迹

更新时间：2026-09-07

## 1. 目标与动机

此前 v3 只标注一次 Controller 选择，不能表示完整时间线生成。本轮改为真实多动作轨迹：第一阶段由 DeepSeek 在冻结语料上执行 SEARCH、VERIFY、MERGE、STOP；第二阶段用 train Gold 帮助教师发现 gap，再实际执行检索闭环。学生训练数据不能看到 Gold 或运行预算。

## 2. 新协议

模型输入包含 dataset、topic、phase、events、memory、valid_actions，以及按步骤可选的 active_gap 和 tool_observation。events 始终按 time、event_id 排序。程序递归拒绝 budget、query_budget、queries_left、rounds_left。

GAP_REFINEMENT 初始化时执行 GAP_MEMORY，模型从当前事件序列中标出时间断裂、因果断裂、关键事件或因素缺失和证据冲突。每个 gap 先生成简短、具体的 thought，再执行 SEARCH。query 为空字符串表示无需搜索，仍保留显式的 SEARCH、VERIFY、MERGE 跳过记录，然后进入下一 gap。

query 非空时对多个结果执行 VERIFY，再由 MERGE 为每个候选选择 APPEND、UPDATE 或 DROP。UPDATE 另调模型融合，并校验事件 ID、日期、证据并集与置信度。MERGE 后再次执行 GAP_MEMORY，记录 resolved_gap_ids 和新出现的 gap。

## 3. 标注来源

第一阶段不使用 Gold，全部标签来自 DeepSeek 的真实执行过程和冻结 BM25 工具返回。第二阶段的教师可比较第一阶段结果与 train Gold，用于 GAP_MEMORY 和查询方向；输出 thought 不得出现 Gold、参考答案、标准答案等字样。VERIFY/MERGE 只看当前时间线和真实检索证据。私有 gap—目标事件对齐存入 teacher_alignment.private.json，不进入 SFT messages。

## 4. 文件作用

- chronos_repro/src/chronos_repro/tisa_data.py：新增 GAP_MEMORY 动作和工具。
- chronos_repro/src/chronos_repro/tisa_rollout.py：输入构造、事件排序、thought/gap 校验、空查询语义和动态 gap 更新。
- chronos_repro/scripts/run_full_timeline_api_agent.py：公共 prompt 改用 thought，模型状态移除 budget，统一 events。
- chronos_repro/scripts/run_tisa_two_phase_annotation.py：两阶段执行、教师私有边界、重试、断点恢复、SFT 编译和评测。
- chronos_repro/configs/tisa_two_phase_annotation_v4.json：Egypt pilot 配置；环境上限不进入模型输入。
- chronos_repro/tests/test_tisa_rollout.py：覆盖输入、排序、空查询和新 gap。
- chronos_repro/artifacts/tisa_two_phase_egypt_pilot_v4：轨迹、预测、评测、SFT、manifest 和私有侧车。

## 5. 错误、分析与修复

第一次停止在第二轮 MERGE。DeepSeek 返回旧字段 reflection 或空 thought，v4 严格校验失败。修复为兼容读取 reflection 后立即规范化成 thought，并从已保存的 VERIFY 继续。

第二次停止在 GAP_MEMORY 初始化。模型生成的某个 gap 描述超过 180 字符。修复为保留模型原文的首个短片段，并让恢复器识别第一阶段已经 STOP，直接继续第二阶段。

两次都不是余额不足。第三次从断点继续成功，没有重跑已完成阶段。

## 6. 真实结果

- 状态：ok
- 最终动作步骤：17
- 最终时间线事件：16
- SFT v4 样本：17
- 第一阶段：SEARCH → VERIFY → MERGE → SEARCH → VERIFY → MERGE → STOP
- 第二阶段：GAP_MEMORY → SEARCH → VERIFY → MERGE → GAP_MEMORY → SEARCH → VERIFY → MERGE → GAP_MEMORY → STOP
- DeepSeek 逻辑调用：17；HTTP 尝试：23；总 token：150,700
- 201 个对齐事件匹配 1 个，event recall 约 0.50%
- 122 个 Gold 日期覆盖 8 个，date recall 约 6.56%

验收：54 项 pytest 全部通过；17 条 SFT messages 均无预算字段；每条 events 有序；教师私有字段未进入 SFT messages；git diff 格式检查通过，仅出现既有 CRLF 提示。

## 7. 结论与下一步

pilot 已证明完整多动作协议可执行，并证明 MERGE 后可以继续维护 gap，但不能声称覆盖全部 Gold。覆盖低的直接原因是试跑只处理 2 个 gap，且一次输入 201 个参考事件成本很高。

正式流程应先按年份或阶段把未覆盖事件形成候选批次，再让 DeepSeek 分批初始化 GAP_MEMORY；对每个 gap 循环真实检索直至解决或空查询；增加逐步 checkpoint 和调用缓存；最后从同状态下的 query、SEARCH/STOP、MERGE 动作和 memory 更新构造局部 DPO 正负例。
