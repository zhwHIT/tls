# Gap 过宽修复与原子检索任务验收

更新时间：2026-09-15。运行代码版本：`two-phase-v8.5-atomic-evidence-gap-contract`。

## 1. 本轮结果与边界

本轮优先落地计划 46 中的 gap 过宽问题，修改初始化 prompt、数据校验、局部搜索输入、更新证明及调度兜底。相关规则已接入真实双阶段执行入口，不是仅新增未被调用的辅助模块。

在 `D:\miniforge\envs\tls\python.exe` 下全套离线回归：**287 passed in 11.36s**。没有安装依赖，没有 API 请求，没有训练，没有读取 `.env`，没有重置调用额度或修改冻结语料及历史评测产物。

默认沙箱的命令与补丁操作遇到 Windows 1385。经受控执行批准后，完成了本地只读检查，并使用原有 `apply_patch` 可执行程序实施补丁；Python 仅用于调用该补丁程序，不通过直接覆盖源码完成编辑。已有未提交改动全部保留。

当前不能声称严格 gold 日期召回已经提高：本轮没有在线实测。287 项测试证明结构约束、重试、状态流转和既有功能回归通过，不等于摘要或决策语义质量已经通过真实模型验收。

## 2. 已复核的历史原因

本轮读取了旧轨迹的 `final_gap_memory`，发现：

- Yemen 一个 gap 同时询问失业、腐败、地区矛盾等多项背景驱动因素，缺少单一的完成条件。
- MJ 一个 gap 要求填补出生至死亡之间 51 年职业与个人生活；其目标已经从局部修复扩张为整个人物生平。
- David Bowie 的 gap 涵盖童年、早期乐队与录音经历，或跨越数十年的专辑及影视作品，难以用少量检索定义完成。

代码层面的对应缺口：

1. 原校验器主要检查类型、描述长度与锚点 ID，不要求具体待查事实或结束条件。
2. 原更新只返回 `resolved_gap_ids` 即可将 gap 标为完成，没有必须引用的解决证据。
3. 原紧凑视图只有普通描述；即使新增约束字段，也可能在投影时被删除。
4. 局部修复仍携带全局阶段摘要，容易把模型重新引向全局背景探索。
5. 单个困难 gap 可以反复占用搜索轮次，缺乏清晰的“暂缓但未解决”记录。

## 3. 新的 gap 契约

每个新 gap 必须包含 `retrieval_target`：

```json
{
  "question": "Was the regulatory approval required by the Aurora agreement granted?",
  "anchor_quote": "subject to regulatory approval",
  "completion_criterion": "A sourced report states whether the required approval was granted.",
  "seed_query": "Aurora purchase agreement regulatory approval decision"
}
```

这是虚构的 few-shot，不是待评测主题的隐藏安排。

字段作用：

- `question`：只查一个具体事实，而非整个事件背景。
- `anchor_quote`：必须逐字出现在至少一个锚点事件的摘要中，防止凭空构造检索议题。
- `completion_criterion`：明确什么证据能够回答问题；不能只写“缺口填补完毕”“信息足够”。
- `seed_query`：可执行的初始搜索建议，不伪装成已经执行过的历史查询；控制器仍可改写。

默认生产初始化和更新启用该契约。底层工具保留旧结构的兼容读取入口，旧档案不自动变成合格训练数据；新搜索入口对缺少契约的 gap 会在请求模型前报错。

### 结构检查与 prompt 各自负责什么

代码检查字段类型、长度、锚点存在性、引用逐字匹配、多问号及明显的宽泛议题表达。prompt 要求单一事实问题、有依据的因果线索，以及粗时间节点不被误判为完全未知。

规则并不能完整判断语义：一个长问题可能只含一个问号，引用真实句子也不保证推论正确。因此仍需后续模型实测与语义审核，不能将关键词检查当作完整语义证明。

## 4. 更新、去重与暂停规则

### 4.1 解决必须附证据

`resolution_evidence` 包含 gap ID、当前事件 ID 和该事件摘要的逐字引用。局部更新只允许解决当前 active gap，不允许顺便关闭未分析的其他 gap。

- 找不到引用、引用不匹配、事件不存在：进入原有有界修复流程。
- 引用事件仍标记冲突：不能据此宣告解决。
- 尚未解决：`resolved_gap_ids` 与 `resolution_evidence` 均为空。
- prompt 还要求引用必须回答 `completion_criterion`；结构校验不能代替这一语义判断。

### 4.2 新 gap 必须与当前修复相关

更新后新增的 gap 至少关联当前锚点或本轮 APPEND/UPDATE 的事件，禁止局部更新再次生成不相关的全局探索清单。每轮新增数量仍沿用原来的最多 2 个。

同一锚点区间和相同 gap 类型视为一个修复任务，重复提议不重新进入队列，从而避免换一个描述或 ID 后继续搜索同一问题。原提议完整保存在 `duplicate_gap_proposals`，并记录对应已有 gap，不删除历史证据。

### 4.3 暂缓不是解决

新增 `DEFERRED`，默认每个 gap 最多进行 3 次实际搜索后，如仍未解决则暂缓。参数 `phase2_max_attempts_per_gap` 可配置，非法值在任何模型调用前拒绝。

暂缓 gap：

- 留在外部 memory 和日志里；
- 保留已执行查询；
- 不继续抢占本轮队列；
- 仍计入终止时的未完成数量；
- 不能变成成功 STOP 的 SFT 正例。

次数限制只是成本兜底，解决 gap 过宽主要依靠前面的具体问题与证据契约，不能以降低搜索次数替代质量改进。

## 5. 上下文调整

- `retrieval_target` 保留在实际发送给模型的 gap 视图中。
- 局部事件排序参考具体 question，而不仅是宽泛 description。
- 第二阶段局部搜索和更新不再重复携带第一阶段全局 `stage_outline` 与全局近期查询；保留该 gap 的查询历史、局部事件和未展示数量。
- 完整全局阶段、事件、引用和搜索记录仍在外部状态里，不因投影而删除。

这不是 4K token 验收已经完成：当前项目仍需确定学生检查点和 tokenizer，并对包括 prompt、few-shot、工具规范在内的整个输入做计数。

## 6. 文件作用

| 文件 | 本轮修改 |
|---|---|
| `src/chronos_repro/gap_contract.py` | 新增具体 gap 契约、few-shot、宽泛问题检查、锚点与解决证据校验 |
| `src/chronos_repro/tisa_rollout.py` | 保留并验证契约，重复提议归档，暂缓状态与审计 |
| `scripts/run_tisa_two_phase_annotation.py` | 接入初始化、搜索、更新 prompt 和校验；新增局部关联及未完成终止处理 |
| `src/chronos_repro/compact_context.py` | 压缩后继续保留具体检索目标与暂缓原因 |
| `src/chronos_repro/two_phase_state.py` | 局部修复不重复全局摘要，保留局部历史与暂缓数量 |
| `src/chronos_repro/runtime_profile.py` | 标记 v8.5 及新默认行为，便于源码绑定和实验溯源 |
| `tests/test_atomic_gap_contract.py` | 新增 25 项参数化及集成回归用例 |
| `tests/test_gap_temporal.py` | 更新旧时间检索夹具，验证新契约与真实本地 BM25 回放共存 |

## 7. 验证命令与结果

```powershell
cd D:\paper\chronos_repro
& D:\miniforge\envs\tls\python.exe -m pytest -q
```

最终结果：`287 passed in 11.36s`。覆盖宽泛问题、多个问题、空泛完成条件、缺失字段、伪造引用、冲突证据、局部越界、重复提议归档、暂缓处理、实际调用入口的有界重试与 STOP 训练过滤。

`git diff --check` 返回 0；存在既有工作区的 LF/CRLF 提示，没有为此执行全仓格式化。许多项目文件原本未跟踪，不能仅用 git diff 代表所有新文件已验证；本轮新文件由 pytest 实际加载执行。

测试使用本地假模型响应或本地索引，不调用真实模型。历史轨迹只读查看，没有重新导出或覆盖。

## 8. 尚未完成的工作与下一步

1. 第一阶段显式 `time_filter` 的统一输入输出契约：当前旧时间检索字段仍为 `date_filter`，尚未完成全链路迁移，不能宣称计划 46 的第一阶段协议已经全部落实。
2. 独立 DeepSeek 摘要更新接口：本轮修改的是 gap memory，第一阶段粗时间事实与正式日级事件的全面分流仍待完成。
3. 固定 Qwen 检查点与 tokenizer，落实 4K 总长度、512 输出上限；不能直接用 DeepSeek token 计数替代。
4. 先用保存产物检查新摘要忠实度，再在明确主题和额度授权后做少量真实模型对照。记录 gap 接受率、修复次数、相关证据增益、暂缓比例、严格日期召回、精确率和自主停止情况。
5. 小规模实测发现的问题修复后，再批量构造 SFT/DPO。训练、验证、测试按主题隔离。

补充纠正计划 46 的 DPO 长度说明：两个分支分别为 prompt+chosen、prompt+rejected，单分支各不得超过 4K；不是把两个回答顺序拼成一个序列。这不代表 DPO 的计算成本与一条 SFT 样本相同。

Yemen 历史调用上限已经耗尽，本轮没有新增 API 权限或额度。不得通过换目录、重置账本或缓存回放绕过限制。新代码改变源码指纹，不应直接重放旧运行目录并视为同版本实验。

相关计划：[46_双阶段摘要与搜索控制器_当前计划进度及漏洞审查](46_双阶段摘要与搜索控制器_当前计划进度及漏洞审查_20260915.md)。历史在线结果仍以 [45_授权实测结果与覆盖优化验收](45_授权实测结果与覆盖优化验收.md) 为准。
