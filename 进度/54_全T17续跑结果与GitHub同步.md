# 全 T17 续跑结果与 GitHub 同步

核验日期：2026-09-16。运行版本：v9.5，状态续跑。

## 结果

调度已于北京时间 15:41 结束，7/9 个主题状态为 ok；Haiti、Iraq 达到新增 300 次请求上限。全主题 aggregate 为 null，尚未完成整体验收。

| 主题 | 状态 | 日期命中 | 日期召回 | 事件数 | 本轮新增请求 |
|---|---|---:|---:|---:|---:|
| bpoil | ok | 68/118 | 57.63% | 129 | 221 |
| egypt | ok | 20/20 | 100.00% | 88 | 271 |
| finan | ok | 22/65 | 33.85% | 81 | 143 |
| h1n1 | ok | 15/21 | 71.43% | 107 | 34 |
| haiti | 请求上限停止 | 11/11 | 100.00% | 55 | 300 |
| iraq | 请求上限停止 | 34/155 | 21.94% | 94 | 300 |
| libya | ok | 40/59 | 67.80% | 192 | 106 |
| mj | ok | 33/38 | 86.84% | 69 | 67 |
| syria | ok | 38/86 | 44.19% | 223 | 115 |

累计保存 1038 条事件，本轮新增 1557 次 HTTP 请求，含父运行累计 4433 次。请求量以持久化账本为准，包含未形成成功响应的网络尝试。所有本轮及继承缓存响应中的最大输入为 3712 tokens；未来训练模型仍须使用自身 tokenizer 验证。

## 如何解释

- 完成主题中 Egypt、H1N1、MJ 达到日期召回 70% 目标；Haiti 的未完成轨迹也达到该数值，但不能计入完整流程验收。
- 7 个 ok 主题的第二阶段均因 runner_limit 结束，不能据此证明自主停止。Finan 第一阶段为 invalid_policy_handoff，其余主题第一阶段均为 runner_limit。
- 本轮未再因上一轮的 Gap 协议、Goldsmith 误判或检查点内存异常中断；Haiti、Iraq 的剩余阻塞是请求预算。
- 日期集合重合不等于事件语义正确。低召回主题和自主停止仍需改进；这些已经多次查看的开发主题不能当成未接触测试集。
- 本轮承接 v9.4 状态，不属于统一 v9.5 从零运行的公平基线。

## 已验证与同步范围

本次同步前完整离线测试再次通过：330 passed。同步框架源码、脚本、测试、配置、已有进度文档和本轮轻量评测记录；API 密钥、模型权重、原始响应缓存和大体积运行轨迹保留本地。

原始轨迹、请求账本及父状态位于本地 `chronos_repro/artifacts/tisa_v95_continuation/` 与 `tisa_v94_all_t17/`。GitHub 中保存本轮最终评测、调度状态、恢复验证和运行摘要，足以核对本表；仅克隆代码不能直接恢复本机缓存和中断状态。

证据：[最终评测](../chronos_repro/artifacts/tisa_v95_continuation/batch/evaluation_after_09.json)、[调度状态](../chronos_repro/artifacts/tisa_v95_continuation/batch/batch_status.json)、[运行摘要](../chronos_repro/artifacts/tisa_v95_continuation/progress_summary.json)、[恢复验证](../chronos_repro/artifacts/tisa_v95_restore_validation.json)。

下一步优先分析低召回主题的检索与日期证据损失，以及阶段上限前未主动停止的原因，再决定后续实验预算。
