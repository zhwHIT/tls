# 第一阶段文档驱动 Memory 与自主发散检索

> 日期：2026-09-11。
> 本次按用户要求撤回 Egypt 专用 task_description，改为冻结 keywords、通用 few-shot、返回文档驱动的探索记忆。
> 本文的配置与逻辑取代 30 号文档中的“新增政治危机 task_description”方案；旧试跑产物不改写。

> 后续状态：完整试跑已完成，83 步、94 个事件；新增逐条拒绝校验与证据日期诊断，见 [32_Egypt文档Memory完整试跑与诊断](32_Egypt文档Memory完整试跑与诊断.md)。本文保留最初接入与小规模检查记录。

## 1. 问题与改造动机

旧第一阶段最多搜索 2 次，实际 API 主要看到主题名、当前时间线和历史 query。它没有把每次检索片段中的事件、时间和主题线索系统地提取为探索 memory，导致初始查询偏向通史；第二阶段只好在过窄的骨架上补缺。

本轮不通过人为写入“政治危机、抗议、政府更替、军方过渡、选举”纠偏。Egypt 冻结快照实际 keywords 只有 `egypt`、`egyptian`，按原样读取，不人工扩充。任务范围必须从返回证据中逐步发现。

目标是先建立粗略的开始—中间阶段—较晚结果框架，不在第一阶段开展细粒度因果反思。框架完整性仍需运行验证，不能由增加轮数直接推定。

## 2. 新的第一阶段流程

```text
topic + 冻结 keywords + 空时间线/空探索 memory
    ↓
SEARCH：通用 few-shot 引导模型生成 query 与探索方向
    ↓
本地 BM25 + 多语言 Dense 混合检索，返回 Top-8 文档片段
    ↓
MEMORY_UPDATE：模型从片段提取事件、时间、参与者/关键词，更新粗阶段框架
    ↓
程序校验文档 ID、原文引文、时间格式与字段，合入探索 memory
    ↓
VERIFY：依据当前证据及完整可见状态验证候选
    ↓
MERGE：APPEND / UPDATE / DROP，形成按时间排序的正式事件序列
    ↓
下一轮 SEARCH 或自主 STOP
```

`MEMORY_UPDATE` 是新增的记录动作。它与第二阶段的 `GAP_MEMORY` 不同：前者记录“搜到了哪些事件/时间、还有哪些大阶段值得探索”；后者分析时间线中的不连贯、缺失与冲突。

`MEMORY_UPDATE` 是实际模型调用，不是程序套规则伪造 LLM 标注。每个非空检索周期因此新增一次记忆提取调用。程序生成的强制 STOP、无候选 MERGE 不作为模型输出训练目标。

## 3. Memory 保存什么

| 字段 | 含义 | 生成方式 |
|---|---|---|
| keywords | 冻结数据集原始关键词 | 程序读取 keywords.json |
| observed_events | 返回文档支持的暂定事件及其时间 | 模型抽取、程序校验后追加 |
| discovered_keywords | 证据中出现的参与者、机构、术语 | 模型提取，校验文字出处，单轮最多保留 20 个 |
| stage_outline | 已发现的大致阶段、时期描述及出处 ID | 模型结合累计观察更新 |
| next_search_directions | 更早、更晚、中间空白、不同侧面等粗粒度方向 | 模型生成，不当作已知事实 |
| boundary_assessment | 当前起点与较晚结果是否已探索、仍有什么不确定性 | 模型简短说明 |
| skeleton_ready | 模型对粗骨架就绪的判断 | 模型输出，程序另加停止门控 |
| search_history | query、方向、返回 ID、新文档数、新观察数 | 程序真实记录 |
| observed_date_range | 已观察事件时间的最小/最大值 | 程序从观察记录计算，不是完整语料范围 |

单条观察包含：`observation_id`、`summary`、`event_time`、`time_expression`、`document_id`、`evidence_quote`、`publication_date`、`verification_status=PROVISIONAL`。

关键边界：

- 暂定观察不自动进入最终时间线，必须经过 VERIFY/MERGE。
- 日期可以是 YYYY、YYYY-MM、YYYY-MM-DD，保留原始精度；不能确定则用 null。
- 原文有“周年”“某月某日”“几年前”等表达，但不足以可靠规范化时，允许 event_time=null，同时保存原始 time_expression。
- 文章发布日期独立记录，不用发布日期代填事件日期。
- 引文必须存在于本次文档标题或片段中；已知事件时间对应的原始表达必须存在于引文中。
- 引文存在性检查不能证明摘要和日期规范化语义一定正确，观察仍标为暂定。
- 当前去重是文档 ID、事件时间、摘要文本的组合去重，不是跨文档语义事件去重。

## 4. 如何引导模型发散 query

不规定 Egypt 应有哪些阶段，也不提供 Gold 事件或日期。few-shot 使用虚构的航天任务、铁路项目，演示以下方向：

| strategy | 作用 |
|---|---|
| DISCOVER | 根据 topic、keywords 做首次宽泛发现 |
| EARLIER | 由已有事件寻找更早背景、起点或前一阶段 |
| LATER | 检查已有末端之后的发展，不能直接把最后一次命中当作终点 |
| INTERVAL | 搜索已有阶段之间缺少代表性事件的时段 |
| FACET | 利用文档中发现的参与者、机构、主题变换查询角度 |
| OVERVIEW | 搜索跨阶段回顾或概述 |
| COMPLETE | 仅用于模型自主 STOP |

few-shot 中的年份和参与者只是演示，不准复制到目标任务。真实 query 应使用本任务证据或关键词。程序禁止规范化后相同的 query 重复执行；更强的语义去重尚未实现。

第一阶段目前通过 query 中的时间/阶段词发散，仍调用既有混合检索，不自动添加硬日期过滤。关键词命中及向量排序不能保证指定时段一定有结果，因此需检查实际返回日期、文档新颖度和记忆中的阶段分布。第二阶段继续使用已实现的 none/soft/hard 文章发布日期窗口。

## 5. 搜索次数、自主停止与安全上限

v5 当前配置从最多 2 轮改为：

- 第一阶段至少 6 次搜索后才开放自主 STOP。
- 运行安全上限 16 次搜索，避免无限调用；不是要求固定执行 16 次。
- 至少 4 个正式时间线事件、3 个模型描述的粗阶段。
- 需要尝试 EARLIER 和 LATER 两类边界探索。
- memory 必须标记 skeleton_ready=true，且不存在剩余粗搜索方向。

上述就绪条件满足后，仍由模型选择 SEARCH 或 STOP。轮数等运行预算不进入 student 输入和 SFT；模型只看到当前允许动作与内容状态。

达到安全上限时由程序生成带 forced=true 的中断记录，并标记 phase1_termination=runner_limit。这种 STOP 不进入 SFT，不包装成模型自主完成。自主停止记为 autonomous_stop，可以保留为待质量审核的模型样本。

阶段数量、策略标签及 skeleton_ready 仍有模型自报成分；这些门控不能保证完整覆盖，需要后续审计阶段与证据。

## 6. 状态一致性与跨阶段使用

新第一阶段 SEARCH/MEMORY_UPDATE 使用同一份 student-visible state；VERIFY/MERGE/UPDATE 的状态适配器现在也读取该状态，避免旧版简化 memory 漏传。API 的外层工具说明、schema 与 few-shot 和 SFT 的简化 messages 仍非逐字相同，不声称是完整 HTTP 请求逐字回放。

第二阶段初始化与后续 gap memory 中保留 `memory.exploration`，包含第一阶段事件观察、阶段概述、关键词和搜索历史。第二阶段 VERIFY/MERGE 同步接收动态 gap memory。当前配置 `phase2_teacher_guidance=false`，不向实际策略发送 Gold。

第一阶段并不会替代第二阶段：前者建立粗阶段广度，后者处理关键缺口、冲突与因果连接。

## 7. 文件作用

| 文件 | 作用 |
|---|---|
| chronos_repro/src/chronos_repro/exploration_memory.py | 通用发散说明、few-shot、关键词读取、记忆校验/去重/更新、query 校验、停止门控 |
| chronos_repro/scripts/exploration_phase.py | 第一阶段真实执行循环，以及 MEMORY_UPDATE/SEARCH 提示构造 |
| chronos_repro/scripts/run_tisa_two_phase_annotation.py | 接入新阶段、统一状态、跨阶段记忆传递、SFT 动作导出、保存最终探索 memory |
| chronos_repro/scripts/run_full_timeline_api_agent.py | 优先使用当前完整 model_visible_state 的适配器 |
| chronos_repro/configs/tisa_two_phase_temporal_v5.json | 移除 Egypt 专用描述，启用新探索模块与轮数/阶段条件 |
| chronos_repro/tests/test_exploration_memory.py | 引文、时间、关键词、去重、停止、状态传递、余额保护的离线测试 |
| chronos_repro/scripts/smoke_exploration_memory.py | 复用已授权旧片段的小规模真实记忆→query 检查；默认只准备请求，--execute 才调用 API |
| chronos_repro/artifacts/exploration_memory_egypt_smoke_v1/ | 本轮检查请求、响应缓存、修复前报告和最终报告；不是新的完整轨迹 |

## 8. 验证范围与后续验收

离线测试覆盖真实执行器的 SEARCH→MEMORY_UPDATE→VERIFY→MERGE 顺序，以及连续两轮的状态传递；模型和检索结果采用 fixture，不把此测试作为检索效果结论。

真实冒烟检查复用旧 Egypt 轨迹第一次检索的 8 篇已授权片段，先生成探索 memory，再生成下一条 query，最后仅本地执行该 query。新增检索结果不再次发送给 API。本检查没有新跑完整第一阶段，不报告改进后的 Gold 覆盖率。

已修复的两个本地问题：新 memory 分支不应提前求值旧 search_history 默认字段；未知规范化日期不应强制丢弃原文时间表达。关键词容量改为校验出处后的去重截断，不因超过 20 个重新生成整份记忆。原始响应缓存保持不变。

下一步完整试跑需要检查：

1. 相同冻结语料下，第一阶段的事件/阶段时间分布是否比旧 2 轮更宽。
2. EARLIER/LATER/INTERVAL/FACET 是否实际带来新文档、新事件，而非只换标签。
3. 第一阶段是否自主停止，还是达到 16 轮上限；不得混用为 STOP 正例。
4. 骨架观察、正式事件、Gold 评测三者分别统计，不能把暂定记忆当作已验证召回。
5. 再执行第二阶段，比较纯增加轮数与“增加轮数 + 文档 memory”的效果，区分收益来源。

## 9. 运行命令

在 D:\paper\chronos_repro 下，使用 tls 环境；没有新增依赖安装。

```powershell
conda activate tls
$env:PYTHONPATH='src'
python -m pytest -q

# 新目录运行，不覆盖历史 Egypt v5 pilot。
python scripts/run_tisa_two_phase_annotation.py --project-root . --config configs/tisa_two_phase_temporal_v5.json --env-file .env --output-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1
```

以上完整轨迹命令仅作为后续入口。本轮实际是否完成真实冒烟检查，以 report.json 和本文后续结果节为准。余额不足立即停止；可修复的结构错误有限重试，已有合法缓存优先复用。

## 10. 本轮实际验证结果

真实检查已完成，`artifacts/exploration_memory_egypt_smoke_v1/report.json` 的 status=ok。

- 输入：旧第一次 SEARCH 的 8 篇已授权片段，初始 events 为空，keywords 仅 egypt/egyptian，无专用 task_description，无 Gold。
- 从缓存复验并采用的 memory：9 条暂定观察、20 个有文字出处的关键词、6 个粗阶段、5 个后续探索方向。
- 9 条观察中，仅 2 条保留可规范化的事件时间（2011-01、2012-06-24），其余为 null；没有用文章日期填充未知事件日期。
- skeleton_ready=false。模型识别到起点之前、2011 年中间阶段、2012 年 6 月之后仍缺少探索。
- 下一步自主选择：strategy=LATER，query=`Egypt after June 2012 Morsi presidency timeline`。
- 本地混合检索返回 8 篇，其中 7 篇相对初始检索为新文档，发布日期包括 2012-11-23、2012-11-26、2013-06-30、2013-07-02/03/05/06。
- 这些是新文档命中，不是已经通过 VERIFY/MERGE 的新增事件。本检查没有发送这些新片段给 API。

这支持一个有限结论：模型可以从真实检索文档形成 memory，并据此将查询推进到旧时间线末端之后。尚不证明完整第一阶段、完整两阶段的质量或 Gold 召回已经提升。

### 10.1 调用与失败修复记录

第一次尝试在 API 之前遇到本地旧字段求值错误，修复后才真正发出请求。

实际共获得 4 个 API 响应：3 个来自 memory 提取及修复尝试，1 个用于下一条 query。前 3 个响应原封不动保留。初始 memory 校验因“未知日期不得保留原始时间表达”而失败；该规则修复后离线复验缓存。另发现关键词数量超限，改为校验来源后截断；包含无出处关键词的缓存仍被拒绝，没有放宽事实出处要求。

最终显式复用 `8b710a579b4bf8c0647c065dd9c81055c0a731f83ce2f4495c39bbb032b0fed0.json` 的记忆响应，未再次付费提取 memory。最后一次进程只新增 1 次 query 调用，因此 `new_calls_this_run=1` 不能当作全轮调用数；`manually_reused_memory_response` 单独记录显式缓存复用。

4 个缓存响应累计用量为 37,996 tokens：prompt 8,510，completion 29,486。未触发余额不足错误；未查询或声称核验账户余额。修复前失败报告保存在 `pre_repair_report.json`。

### 10.2 最终离线验证

完整测试为 **115 passed**，新增模块、执行器与冒烟脚本通过 Python 语法编译检查。随后为未来 memory 提取请求显式补充输出数量限制，减少格式重试；已完成的模型检查未因这一提示补充而再次运行。

旧完整 pilot 的 trajectory、prediction、evaluation 和 run_config 均保留。新一轮完整轨迹尚未启动，应使用第 9 节的新输出目录。
