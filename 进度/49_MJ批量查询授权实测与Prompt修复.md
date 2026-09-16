# MJ 批量查询授权实测与 Prompt 修复

日期：2026-09-15。主测试版本 v9；修正及定点复测版本 `two-phase-v9.1-explicit-json-and-thought-bounds`。

## 1. 结果与边界

用户明确授权第 48 份进度中列出的 MJ 新闻片段、标题、来源标识及派生状态向 DeepSeek 外发后，已完成本轮授权范围内的测试。

- 原主流程使用 21 次 HTTP 请求，在 GAP_DISCOVERY 字符长度校验失败后停止，保留 stopped_error。
- 修正 prompt 后使用剩余 3 次请求做定点复测，三个用例均一次通过。
- 两部分共享同一个 request_ledger.json，累计 **24/24**，没有重置额度，没有继续外发。
- 定点复测单独记录源码指纹与原轨迹哈希；原失败轨迹未覆盖。
- 全套离线回归：**306 passed in 33.19s**；git diff --check 返回 0。

本轮证明了小规模批量查询、延迟摘要及修正后 gap/query 输出的可执行性，不能声称完整第二阶段、最终时间线或自主停止已经通过验收。

## 2. 批量查询与延迟摘要的真实表现

| 批次 | 实际查询数 | 事件修订数 | 学生看到的摘要版本 | 学生看到的事件增量数 |
|---|---:|---:|---:|---:|
| 1 | 2 | 3 | 0 | 0 |
| 2 | 2 | 3 | 0 | 3 |
| 3 | 2 | 0 | 0 | 5 |

三个批次结束后才调用一次 MEMORY_UPDATE。共保留 5 个事件、4 个日期；事件修订包含对既有死亡事件的 UPDATE。摘要将这 5 个事件整理为带 event_ids 的事实列表，没有添加 gap 或查询计划。

第三批没有新增事件修订，查询存在近义重复。这说明允许多个 query 和减少摘要频率已经工作，但如何避免语义重复、如何判断继续检索价值仍需优化。第一阶段因 3 批上限停止，不能作为自主 STOP 成功样本。

原诊断结果命中 2/38 个参考日期（5.26%）。这是受限烟测和中断结果，不能与历史完整 MJ 运行的 97.37% 直接比较，也不能据此判断新框架提高或降低覆盖率。

## 3. 实测发现的 Prompt 问题与修复

### SEARCH 的 null 类型

原 output 示例将 stop_reason 描述为字符串说明，模型两次输出字符串 "null"，触发修复。已将 SEARCH 示例设为真正 JSON null，并补充完整 SEARCH/STOP 示例；system 明确禁止字符串 "null"。错误提示同时使用当前 query_limit，避免固定写 1–3 与配置不一致。

### GAP_DISCOVERY 的 thought 长度

原 prompt 只写“short supported analysis”，校验器却要求 6–240 字符。两次真实响应分别为 **340 和 248 字符**，导致阶段二初始化失败。

修正后 API system 和 gap output 示例都明确：单句、建议不超过 160 字符、硬上限 6–240 字符（含空格）。没有提高校验上限，也没有通过截断旧输出伪造通过。

### 定点复测结果

1. 初始状态 SEARCH：一次通过，2 条查询，stop_reason 为 JSON null。
2. GAP_DISCOVERY：一次通过，识别 1 个具体 gap——“最终确定的 Michael Jackson 死因是什么？”锚点是“尸检尚未给出死因”的已有事实。
3. 针对该 gap 的 SEARCH：一次通过，输出关于官方死因裁定和尸检结果公布的两条查询，stop_reason 为 JSON null。

复测只验证 prompt 输出，没有执行这两条新查询，没有新增事实，也没有完成缺口补充。

## 4. DeepSeek 实际 token

全部 24 次响应均有服务端 usage，输入范围 **551–2281 tokens**；学生输入最大 **1579**，学生输出最大 **107**。全部符合本轮输入不超过 4096、学生输出不超过 512 的要求。

| 模块 | 调用数（含格式修复） | 最大输入 tokens | 最大输出 tokens |
|---|---:|---:|---:|
| BATCH_POLICY | 7 | 1579 | 107 |
| VERIFY | 7 | 2281 | 1049 |
| MERGE | 5 | 930 | 221 |
| UPDATE | 1 | 947 | 136 |
| FACT_MEMORY | 1 | 601 | 208 |
| GAP_DISCOVERY | 3 | 1366 | 231 |

累计响应计数：输入 **28131**，输出 **6710**，合计 **34841 tokens**。没有为达到 4K 而填充无关内容。

这些是当前小状态的实测，不等于长主题或 Qwen tokenizer 已验收。工具模块输出限制与学生输出限制分开，VERIFY 输出超过 512 不属于学生输出超限。

另发现原 trajectory.usage 只计入 19 次已归档调用，未包含最后失败的两次 gap 请求；原 token_usage_audit 和 API 缓存完整保留了 21 次响应。本报告以全部响应审计加同一累计请求账本统计，不使用较小的主轨迹 usage 数字宣称成本。

## 5. 产物

- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/trajectory.json`：原失败轨迹。
- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/token_usage_audit.json`：主流程 21 次响应 token。
- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/prompt_repair_probe/result.json`：修正后的三个定点用例。
- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/prompt_repair_probe/binding.json`：新源码和旧轨迹哈希、共享账本。
- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/acceptance_review.json`：合并后的完整验收与限制。
- `chronos_repro/artifacts/tisa_v9_batch_mj_smoke/request_ledger.json`：24/24。

下一步应验证完整第二阶段检索与有证据的 gap 关闭、自主停止、更多事实累积后的输入长度，并修复失败路径的总 usage 汇总。当前额度已用完，本轮没有进行额外 API 调用或训练。
