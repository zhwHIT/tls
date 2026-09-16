# 批量查询与延迟摘要框架：审核及 API 预检

> 后续状态：用户已明确授权外发，完成 24 次请求及两处 prompt 修复。真实结果和当前限制见 [49_MJ批量查询授权实测与Prompt修复](49_MJ批量查询授权实测与Prompt修复.md)。下文的待授权和 0 次调用描述是本文件记录的历史预检状态。

日期：2026-09-15。版本：`two-phase-v9-batched-search-delayed-memory`。

## 完成情况

新协议已接入真实双阶段入口，学生负责 SEARCH/STOP，固定 API 负责 MEMORY_UPDATE、GAP_MEMORY。旧配置仍走历史入口；新行为由 batch_controller.enabled 开启。

全套离线回归 303 passed in 27.05s；最后的缓存 token、锚点视图等修改完成后，相关回归 83 passed in 3.48s。不能将两个数字相加。未训练模型、安装依赖或修改冻结语料。

MJ 本地混合检索预检通过：121 篇冻结文档，12 条结果，1 个片段。API 启动命令被自动审批拒绝，理由是“虽授权了 API 测试，但未明确授权这类具体数据向该目的地的外发”。已请求用户确认具体数据向 https://api.deepseek.com 外发。当前没有请求账本和响应缓存，本轮 0 次模型 API 请求。

## 框架行为

1. 学生读取事实摘要、版本化事件增量、近期查询、当前 gap 和锚点，输出 1–3 条互补查询或 STOP；本次配置上限 2 条。
2. 每条查询显式包含 time_filter（none/soft/hard，start/end），过滤的是文章发布日期。执行器保存 requested_filter、executed_filter、结果 ID。
3. 同批查询分别检索，重复片段按 ID 去重后 VERIFY/MERGE。联合批次增益不冒充单条查询的独立贡献。
4. 外部事件实时更新；摘要默认每 3 个完成批次检查更新，增量过多或输入压力大时提前更新，阶段交接和最终选材后按需更新。
5. 未更新摘要期间，输入包含 APPEND、UPDATE、DELETE；仅给新增事件不足以维持一致性。摘要提交时校验 revision。
6. API 摘要只生成带 event_ids 的事实列表，不要求制造问题。无事实变化不重复调用。
7. API 在第二阶段识别 gap，允许空列表。优先检查已有事件是否能回答，再执行检索。无新事实不反复全局制造 gap。
8. STOP 表示没有值得继续查询的问题，不表示事实已完整覆盖。未解决 gap 保留为 DEFERRED；上限停止独立标记 forced。
9. 同锚点、同类型的不同问题不再直接合并：去重签名增加规范化 question，纠正此前认为“谁批准”和“何时批准”可无损归并的错误判断。
10. 学生候选只导出 SEARCH/STOP；固定 API 的摘要和 gap 动作仅保留在轨迹。修复提示中的输出不直接进入干净训练候选。所有候选仍需语义审核。

## Prompt 审核

| 原冲突或风险 | 当前处理 |
|---|---|
| 学生同时承担摘要、gap 与查询 | 分离固定 API prompt，学生只有 SEARCH/STOP |
| 无合理问题仍必须搜索 | 取消新入口最低轮数、事件数、阶段数门槛，允许空 gap |
| 提示查询数量与配置不一致 | prompt 的 query_limit 与解析器使用相同上限 |
| 摘要旧事实覆盖事件修订 | 明确 revision、UPDATE 替换及 DELETE 作废 |
| 时间参数沿用旧锚点推导规则 | 新协议直接提供 none/soft/hard 起止范围，不追加旧 date_filter prompt |
| 提取提示承诺一定有续页 | 改为执行器限额允许时可能续页 |
| 新协议投影丢失版本和删除记录 | 保留完整新协议，并继续先检查隐藏参考答案字段 |
| 缓存回放绕过 token 门禁 | 保留原响应 usage 做限额复核，不重复统计 HTTP 调用 |
| 空查询被当成缺口解决 | 新学生协议不用空 SEARCH 表达跳过；显式 STOP 不宣称完成 |

事实摘要最多 8 条、每条 200 字符，允许选择性省略；完整事件仍在外部库。最终选材分页，不承诺跨页全局比较，前面的 MERGE 负责合并。引用匹配和离线测试不是语义正确性证明。

## 约 4K 输入

- 从 DeepSeek 官方文档下载 tokenizer 数据，只加载 tokenizer.json，未执行包中的 Python。
- 完整 system/user 内容加模板余量做离线预检，上限 3800 tokens。
- API 返回后以 usage.prompt_tokens 验收输入不超过 4096；超限响应隔离。
- 学生 max_tokens=512，并检查 completion_tokens；固定 API 输出上限单独设置。
- 输入过长优先摘要更新或事件分页，不静默截断 JSON、关键事件或修订。
- 空策略状态离线估计 644 tokens，不为接近 4K 填充内容。
- 当前没有服务端 token 实测，也不能视作 Qwen 验收。本轮按用户要求控制输入约 4K；输入与输出合计可达 4608，若后续总序列限定 4096，需预留输出空间。

官方依据：[DeepSeek Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)。实际用量以 API usage 为准。

## 文件和测试边界

- `src/chronos_repro/batch_memory.py`：版本化事实摘要、查询历史和事件增量。
- `src/chronos_repro/batch_policy.py`：批量查询、停止和时间过滤契约。
- `src/chronos_repro/token_budget.py`：token 预检、服务端计数与输出限制。
- `scripts/batched_search_phase.py`：真实两阶段执行、摘要/gap API、分页选材。
- `scripts/run_tisa_two_phase_annotation.py`：路由、保护客户端、轨迹与训练导出。
- `configs/tisa_v9_batch_mj_smoke.json`：本次新配置。
- `tests/test_batch_controller.py`：状态、协议、缓存和执行入口回归。

本地预检：`chronos_repro/artifacts/tisa_v9_batch_mj_smoke/preflight.json`。

待用户明确确认的外发范围：MJ 第一阶段最多 3 批、第二阶段最多 2 批；每批最多 2 条 query，每条最多 1 段正文 1200 字符与前文 400 字符，附新闻标题、来源标识、查询和派生状态。向 https://api.deepseek.com 累计最多 24 次 HTTP 请求（含重试），不发送 Gold 参考答案。用户此前已授权 API 测试，但本次自动审批要求进一步明确数据范围和目的地。

余额不足立即停止；保留累计账本和源码绑定。不得通过换工具、目录或重置账本绕过审批；Yemen 历史额度未增加。
