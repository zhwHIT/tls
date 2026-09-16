# v6 Egypt 授权小规模实测与验收

日期：2026-09-14。工作目录：`D:\paper`。Python：`D:\miniforge\envs\tls\python.exe`。

> 后续准备已完成：完整入口接入持久限额及 API 开关，164 项离线测试通过；完整调用范围仍待确认，见 [36_v6完整两阶段预检与调用范围确认](36_v6完整两阶段预检与调用范围确认.md)。本篇保留烟测执行时的 152 项测试和实际 API 结果。

## 1. 任务与授权边界

本轮承接 [34_v6覆盖导向框架与论文检索配置落地](34_v6覆盖导向框架与论文检索配置落地.md)，根据用户“授权”，执行固定证据的小规模真实 API 验证。

- 端点为 `https://api.deepseek.com`，模型为 `deepseek-v4-flash`，从既有 `.env` 读取密钥，文档与日志不写入密钥。
- 固定输入为 `chronos_repro/artifacts/coverage_v6_offline/reader_preview.json` 的 8 个片段，实际来自 7 个父文档；每段正文最多 3200 字符、紧邻前文最多 400 字符，另含标题。
- 本次授权合计最多 30 次 HTTP 请求，包含传输重试和模型输出修复，程序重启不获得额外额度。余额不足立即停止。
- 没有启动配置中的 400 次完整运行，没有新增主题或外发其他正文，没有改动冻结语料、检索索引、Git 全局配置或 Conda 环境。
- Gold 在 API 执行结束后才由本地评测读取，不向模型提供。

本轮链路为固定片段 → 分批 VERIFY → MERGE（必要时 UPDATE 融合）→ FINAL_SELECT → 保存与评测。它不是完整的 SEARCH/STOP、Memory 与 GAP_REFINEMENT 轨迹，不能替代后续端到端验证。

## 2. 实际操作与修复动机

首先运行原烟测入口，同时执行离线回归，原有 146 项测试全部通过。但真实模型首批输出触发 `date_evidence quote/expression is not verbatim source text`，2 次 HTTP 请求后停止，尚无正式事件。

### 2.1 连续片段切口导致日期引文误拒绝

文档 3312 的片段从一个事件句子中间开始，日期在 `context_before` 尾部，事件正文在 `text` 开头。模型引用的是已提供的连续原文，旧校验却只在标题和正文中查找主引文，因而拒绝跨切口引文，也拒绝完全位于已提供前文的日期事件。

修复后按 `context_before + text` 的原始字符顺序拼接，校验主引文及时间表达式。中间不额外插入分隔符，以保留跨单词切口的准确字符序列；标题仍为独立提供的证据。来源审计继续核查前文、正文位置与全文哈希。

这不是允许任意全文证据，也不是用发布日期补事件日期。模型仍只能引用本轮已提供的内容；改写引文、编造年月日仍会被拒绝。该规则只证明字符和日期表达一致，不能单独证明事件语义蕴含。

### 2.2 修复提示缺少定位，重复修复命中失败缓存

原错误没有指出具体候选；连续两个修复请求又可能完全相同，第二次直接命中相同缓存，并未得到新的修正机会。

现在日期校验错误包含 `candidate_id`，修复请求包含修复轮次，明确要求修正指定候选并返回完整批次。缓存仍按实际消息哈希工作，不清空已有响应，也不将无效响应当成有效标注。

恢复试跑后还遇到过 `thought` 超过 240 字符、将双日区间直接定为单日的问题；这些由正常校验与模型修复处理，不通过放松日期精度或直接截断理由来通过。

### 2.3 调用额度跨重启继承

原限制器只限制单次启动。为保证本轮“共 30 次”边界，增加持久 `request_ledger.json`：每次传输前先记账，中断中的请求也保守计入；余额不足状态写入账本，重启后仍拒绝新调用。

首轮已用的 2 次请求由历史 `trajectory.json` 迁入账本。两次暂停的产物分别归档到 `attempts/before_resume_002/` 与 `attempts/before_resume_013/`，成功缓存保持不变。该账本用于本目录的顺序恢复，不支持多个进程并发使用同一输出目录。

第三批修复仍未通过时，累计已用 13 次请求，前 4 个片段已产生 22 个事件。随后完善提示：明确列出允许引用的完整片段 ID，禁止用日期区间端点替代单日事件，修复时提供上一份完整输出和各候选错误列表。新增烟测已提交批次恢复能力，恢复事件池、候选池、动作记录和已处理片段，只继续剩余 4 个片段；该恢复能力仅接入烟测入口，不代表完整两阶段入口已经支持同样的直接恢复。

## 3. 验证结果

最终状态：**ok**。本轮 API 已结束，没有后台调用，没有启动完整 400 请求流程。

| 指标 | 实测结果 |
| --- | --- |
| 已处理片段 / 批次 | 8/8 个片段，4 个批次，来自 7 个父文档 |
| 有效动作记录 | 9 步：4 VERIFY、4 MERGE、1 SELECT |
| 候选池 | 68 条：28 SUPPORTED、40 INSUFFICIENT；包含跨批重复候选 |
| 合并操作 | 25 APPEND、3 DROP、0 UPDATE |
| 最终筛选 | 25 个事件全部保留，未删除；筛选前后结果一致 |
| 最终日期 | 25 个不同日期，2011-01-25 至 2013-06-23 |
| 年份分布 | 2011 年 11 个，2012 年 9 个，2013 年 5 个 |
| 日期依据 | 7 个 explicit，18 个 contextual_year |
| Gold 日期命中 | 22/122，召回 18.03% |
| Date-P / Date-F1 | 88.00% / 0.2993 |
| Gold 事件词面匹配代理 | 18/201，8.96%；不是独立语义覆盖率 |
| 来源与日期表达审计 | valid=true；8 个片段、4 批 VERIFY，0 项失败 |
| 累计 HTTP 请求 | 17/30，含两次恢复前的调用及传输重试 |
| 成功返回并缓存的响应 | 15 份；其中包含后来被校验拒绝的响应 |
| 缓存响应 token 总数 | 211,283：输入 47,325，输出 163,958 |
| 余额不足 | 未发生 |

两次未成功返回响应的传输尝试已计入 17 次请求，但其是否计费及 token 数无法从现有响应确定。上述 token 是收到的 15 份响应的合计，不是账单金额；没有查询或推算费用。`trajectory.usage` 主要累计已完成工具链的审计，遇到终止性校验错误时可能漏掉整批失败调用，因此本轮总用量以缓存汇总与请求账本分别核对。

各批提取结果如下，说明不能只看候选数量判断有效日期覆盖：

| 批次 | 片段数 | 候选数 | SUPPORTED | INSUFFICIENT |
| --- | ---: | ---: | ---: | ---: |
| 1 | 2 | 13 | 5 | 8 |
| 2 | 2 | 23 | 20 | 3 |
| 3 | 2 | 9 | 3 | 6 |
| 4 | 2 | 23 | 0 | 23 |

第二批确实超过旧版六候选上限；第四批虽提取 23 条线索，却没有足够日期依据进入正式时间线，MERGE 因无接受候选走确定性空操作，没有额外模型请求。当前 40 条 INSUFFICIENT 候选需在后续完整流程中通过新证据或 gap 查询补日期，而不是直接填入时间线。

**验收解释：** 固定片段提取、合并与最终选材流程已跑通，来源审计已通过；尚未完成独立事件蕴含审查，未达到项目暂定的完整 Gold 日期召回 70% 目标，也没有验证全量检索、Memory、GAP_REFINEMENT 或自主 STOP。Gold 外的 3 个预测日期不等于 3 个事实错误；Date-P 衡量日期集合匹配，不能直接当作事实正确率。本轮输入范围、文本量和执行流程与历史完整 v5 不同，不能当作公平消融或直接宣称整套检索性能优于 v5。

UPDATE 本轮没有被实际触发，单独模型融合只保留离线测试证据，不能称为已完成真实 API 验证。

离线回归最终为 **152 passed**。新增 6 项测试覆盖连续前文引文、前文独立事件引文、跨重启请求上限、余额不足跨重启禁止调用、候选定位及修复请求差异，以及已提交批次的安全恢复。新增测试夹具中的两个字段遗漏已修正，最终完整回归通过。

## 4. 本轮文件作用

以下路径相对 `chronos_repro/`：

| 文件 | 本轮作用 |
| --- | --- |
| `src/chronos_repro/date_evidence.py` | 对已提供的连续前文与正文校验日期引文 |
| `src/chronos_repro/full_timeline.py` | 在日期错误中附上候选 ID，便于定位修复 |
| `scripts/run_full_timeline_api_agent.py` | 明确连续上下文引用规则，修复提示加入轮次与候选定位 |
| `src/chronos_repro/limited_llm.py` | 可选持久请求账本、请求前记账、余额不足持久停止 |
| `scripts/run_coverage_api_smoke.py` | 继承历史请求量、归档失败结果、恢复已提交批次、阻止已完成烟测重复启动 |
| `scripts/inspect_coverage_smoke.py` | 纯离线检查缓存响应的 thought 长度、候选状态和日期校验错误 |
| `tests/test_smoke_recovery.py` | 新增 6 项离线回归测试 |
| `artifacts/coverage_v6_egypt_smoke/trajectory.json` | 本轮最新动作、模型输入输出、状态、事件及请求计数 |
| `artifacts/coverage_v6_egypt_smoke/candidate_pool.json` | 包含不确定日期线索在内的候选池 |
| `artifacts/coverage_v6_egypt_smoke/event_pool.json` | 最终全局选材前的事件池 |
| `artifacts/coverage_v6_egypt_smoke/prediction.json` | 最终时间线，供本地评测读取 |
| `artifacts/coverage_v6_egypt_smoke/evaluation.json` | 执行结束后的 Gold 日期评测及词面覆盖代理 |
| `artifacts/coverage_v6_egypt_smoke/evidence_reader_manifest.json` | 片段来源位置、哈希与处理状态 |
| `artifacts/coverage_v6_egypt_smoke/evidence_audit.json` | 最终冻结来源及日期表达审计，已通过 |
| `artifacts/coverage_v6_egypt_smoke/cache_diagnostics.json` | 各缓存响应的日期校验错误、thought 长度与用量；不是语义审查 |
| `artifacts/coverage_v6_egypt_smoke/request_ledger.json` | 包含失败重试及历史调用的累计请求数 |
| `artifacts/coverage_v6_egypt_smoke/api_cache/` | 成功返回的原始响应，含后来未通过校验的响应；不是全部可直接训练的数据 |
| `artifacts/coverage_v6_egypt_smoke/attempts/` | 修复重启前的失败产物归档 |

## 5. 运行命令

从 `D:\paper\chronos_repro` 执行。以下烟测命令本轮已执行；完成后不要为查看结果重新调用模型。

```powershell
& D:\miniforge\envs\tls\python.exe -m pytest tests -q
& D:\miniforge\envs\tls\python.exe scripts/run_coverage_api_smoke.py --config configs/tisa_coverage_egypt_v6.json --preview artifacts/coverage_v6_offline/reader_preview.json --env-file .env --output-dir artifacts/coverage_v6_egypt_smoke --allow-api
```

以下两项仅执行本地审计，不调用 API：

```powershell
& D:\miniforge\envs\tls\python.exe scripts/audit_coverage_rollout.py --run-dir artifacts/coverage_v6_egypt_smoke --index artifacts/crisis_collection_v3_hybrid_index.json --topic egypt --output artifacts/coverage_v6_egypt_smoke/evidence_audit.json
& D:\miniforge\envs\tls\python.exe scripts/inspect_coverage_smoke.py --run-dir artifacts/coverage_v6_egypt_smoke --preview artifacts/coverage_v6_offline/reader_preview.json --output artifacts/coverage_v6_egypt_smoke/cache_diagnostics.json
```

## 6. 下一步与研究边界

先看单批提取、日期错误、重复合并与筛选损失，再安排完整两阶段 Egypt 试跑。完整试跑会读取并外发本次 8 个片段之外的证据，需要独立确认外发范围和请求额度；本次授权不等于批准配置里的 400 次调用。

完整实验应同时报告日期召回、Date-F1、独立事件语义覆盖、未读队列、搜索阶段分布、未解决 gap 与 token 用量，并对比筛选前后的结果。只有完整轨迹才能验证 Memory 的使用和自主停止。固定 8 个片段的处理完成率、事件数或来源审计通过率，都不能替代 Gold 事件覆盖率。

本轮不导出 Controller SFT/DPO 数据：缺少 SEARCH/STOP 决策链；工具修复响应也不应直接作为学生策略正例。
