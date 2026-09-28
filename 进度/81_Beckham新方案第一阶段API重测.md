# Beckham 新方案第一阶段 API 重测

2026-09-18，用户明确授权按新方案调用 API 重测。

## 运行范围

- 主题 David Beckham，空时间线开始，仅第一阶段，不运行补充阶段、第二阶段或最终筛选。
- 模型 deepseek-v4-flash；同一冻结语料与混合索引，top-12；句子联合选择和未读候选队列不变。
- 新增人物生涯、成就、事故等 SEARCH 提示，待补证 reason 回流、线索轮转、查询绑定，以及 VERIFY 联合原始证据和重验证。
- 最多 24 批，每批最多 3 条 query；非 VERIFY 请求累计上限 500，VERIFY 单独记账不限次数；输入预检 3800 / 硬上限 4096 tokens。
- 主指标仍为全部 16 个 Gold 日期的精确日期覆盖率；Gold 仅供独立评测，不输入模型。

## 产物

- 配置：`chronos_repro/configs/beckham_joint_evidence_phase1.json`。
- 独立目录：`chronos_repro/artifacts/beckham_joint_evidence_phase1_api`。
- 启动进程 PID 40384；日志 `run.log` 为 UTF-8；`launch_record.json` 保存启动与退出信息。
- 检查点、请求账本保存在该目录。运行已被用户中止，没有生成完整最终轨迹；候选池保存在检查点中，停止审计另存独立目录。
- 状态脚本：`analysis_tools/status_beckham_joint_phase1.py`。
- 评测脚本：`analysis_tools/evaluate_beckham_joint_phase1.py`。

## 比较口径

旧联合选句运行在第 12 批中止，最终保存 24 条事件、1/16 Gold 日期命中。新运行允许自适应 query，完成程度和实际调用量可能不同，因此比较是端到端诊断，不能解释为等预算因果消融。

启动前真实语料预检通过：2,945 篇，返回 12 篇、选中 10 个片段。旧运行目录不改写。

## 用户中止后的实测结果

2026-09-18 15:50:33（北京时间）按用户要求终止，进程退出已确认。9 批完成、第 10 批中断，30 条 query、30 条事件，Gold 日期覆盖率 **1/16 = 6.25%**，仅命中 2010-03-14。共记账 231 次请求。没有继续调用 API。

离线审计发现跨年份比赛误合并、未来 weekday 回指过去、预告变成结果，以及 4 条线索因错误确认被关闭。154/167 个 VERIFY 步骤已联合读取多片段，SEARCH reason 传递也已生效，因此不能继续用旧版接口未接入解释当前全部失败。

完整证据、指标与代码定位见 [82_Beckham新方案停止与结果错误审计](82_Beckham新方案停止与结果错误审计.md)。原始检查点及未完成写入文件均单独保存到 `artifacts/beckham_joint_evidence_stopped_audit`；未修改事件或日期。
