# VERIFY 不限次数与跨数据集授权测试

日期：2026-09-16。版本：v9.7。

用户授权：修改 VERIFY 请求限制，补充 T17 测试，并使用 API 测试 Crisis、Entities。

## 执行范围与当前状态

北京时间 **20:42** 已启动串行后台任务，PID 为 `9000`，共 **53 个主题任务**：

| 数据集 | 本轮任务 | 执行方式 |
|---|---:|---|
| T17 | Haiti、Iraq，共 2 个 | 从 v9.5 未完成状态继续 |
| Crisis | 全部 4 个主题 | 使用 v9.7 新目录，从头运行 |
| Entities | 全部 47 个主题 | 使用 v9.7 新目录，从头运行 |

执行顺序为 T17 → Crisis → Entities。当前是任务启动记录，不是完成报告。每个主题结束后，自动保存总体进度和所属数据集的独立评测；不足余额会停止整个任务队列。

T17 另外 7 个主题沿用原 v9.5 已完成结果，本轮不重复执行。因此本轮两个主题与原七个主题一起查看时，是混合版本的补充结果，不能称为九主题统一 v9.7 从零评测。

Crisis、Entities 此次测试后均属于已观察数据；尤其旧划分中的 Yemen 和跨数据集重复的 Michael Jackson 不能作为未接触测试主题。Gold 不进入推理输入，仍仅用于事后评测。

## 请求限制修改

配置增加 `unlimited_verify: true`。只有实际请求顶层 `stage == VERIFY` 才免于次数预算，原始片段中的文字或嵌套 stage 不改变请求类别。

- VERIFY、VERIFY 格式修复和网络重试均不消耗非 VERIFY 请求预算，但每次实际传输仍计入累计调用。
- 新主题每个最多 **500 次非 VERIFY 请求**；Haiti、Iraq 各最多新增 **300 次非 VERIFY 请求**。
- 账本同时保存 `requests_started`、`budgeted_requests_started`、`verify_requests_started`。
- 旧账本没有分类的数据标记为 `legacy_unclassified_requests`，保守计入历史预算；不回溯伪造分类、不清零历史费用。
- 新账本仍继承旧累计总数：Haiti 378、Iraq 449。新增非 VERIFY 上限分别对应账本预算阈值 678、749，**不是总 HTTP 调用上限**。
- 新目录保留源快照与账本哈希，旧结果不覆盖。重启后计数与余额停止状态继续生效。

取消 VERIFY 次数上限不改变搜索阶段预算、证据校验规则、失败重试策略和上下文门禁。阶段最多 24 个探索批次、18 个 gap 搜索周期；输入预检 3800 tokens、API 输入上限 4096，策略输出最多 512 tokens。

## 预检与测试

- 完整测试 **338 项通过**。
- 新测试覆盖 VERIFY 及修复免计预算、重启计数、余额停止、旧账本迁移、嵌套输入不能伪造免计类别、总调用超过非 VERIFY 上限后的正确调度。
- 53 个主题的 **159 个冻结数据文件**哈希校验通过。
- 离线恢复 Haiti 的 55 条事件、Iraq 的 94 条事件，并确认查询历史不变；分别剩余 4、2 个未完成片段待接续。
- 各主题开始前还会执行真实本地混合检索预检，逐个核对数据、索引、源码绑定。
- 启动后已确认 Haiti 的真实 API 调用：累计请求 378→380，其中新增 VERIFY 2 次，`budgeted_requests_started` 仍为 378。已有新响应保存，证明免计预算在实际传输中生效；该数字仅为启动时快照。
- 本轮运行源码指纹：`d3b63ae0d8fae8ebf160302d828af64794112989b1d463fd9a005801f8bae92e`。

## 文件与查询命令

- [总任务配置](../chronos_repro/configs/tisa_v97_all_suite.json)
- [T17 补充配置](../chronos_repro/configs/tisa_v97_t17_suite.json)
- [Crisis 配置](../chronos_repro/configs/tisa_v97_crisis_suite.json)
- [Entities 配置](../chronos_repro/configs/tisa_v97_entities_suite.json)
- [离线验证](../chronos_repro/artifacts/tisa_v97_offline_validation.json)
- [当前调度状态](../chronos_repro/artifacts/tisa_v97_authorized/batch/batch_status.json)

输出根目录：`chronos_repro/artifacts/tisa_v97_authorized/`。

逐主题完成后生成 `batch/evaluation_after_NN.json`，以及 `batch/evaluation_crisis_after_NN.json` 等分数据集报告。总体队列包含续跑与从头测试两种模式，混合模式不输出正式聚合分数；单独数据集评测仍需等待该数据集所有计划主题完整结束。

```powershell
Get-Content -Encoding UTF8 D:/paper/chronos_repro/artifacts/tisa_v97_authorized/batch/batch_status.json
Get-Content -Encoding UTF8 D:/paper/chronos_repro/artifacts/tisa_v97_authorized/runner.err -Tail 30
```
