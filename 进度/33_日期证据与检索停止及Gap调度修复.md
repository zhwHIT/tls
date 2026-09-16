# 日期证据、检索停止与 Gap 调度修复

更新时间：2026-09-11。工作目录：`D:\paper`。

## 一、本轮结论

针对文档 32 中暴露的问题，已完成代码、提示词和离线验证的修复。本轮没有调用 DeepSeek，没有安装新依赖；Python 操作使用 `D:\miniforge\envs\tls\python.exe`。

目前验证结果为 137 项测试通过。历史 Egypt 轨迹中的两处日期问题已用真实返回片段复核：错误的日级日期被拒绝，正文支持但晚于发表元数据的日期仍可保留。历史 trajectory、prediction、evaluation 的 SHA-256 前后相同。

这不意味着 Gold 覆盖已提高，也不意味着模型已能可靠自主停止。新的完整 API 轨迹尚未运行，不能用代码测试替代真实效果评估。

## 二、问题与修复对应

| 问题 | 根因 | 本轮修复 |
| --- | --- | --- |
| `January 2011` 被补成 `2011-01-01` | VERIFY 只检查 ISO 日期格式；纠错提示要求完整日期，未约束证据精度 | 要求引用原文日期证据；禁止凭空补日；年月级信息保留在 memory |
| 正文事件日期晚于文章发布日期 | 聚合页更新内容与发表元数据不一致 | 不把发布日期上界当作事件边界；日期过滤提示增加警示 |
| 第一阶段不断围绕同一后续事件改写 query | 仅精确字符串去重，且只观察文档/暂定观察是否新增 | 增加相近查询无增益检查、连续策略限制和 merge 后实际增益记录 |
| memory 长期保留待搜方向，难以 STOP | 未区分可行动的粗阶段缺口与多次检索仍未证实的线索 | 增加有查询记录支撑的 `DEFERRED_UNCONFIRMED`，明确“不等于已解决” |
| 高优先级 gap 反复占用检索机会 | 每轮仅按 priority 选择 OPEN gap | 先按已尝试次数少者优先，再按 priority 排序 |
| 第二阶段达到运行上限仍生成模型 STOP 标签 | 程序中断与模型自主完成混在一起 | 有未解决 gap 时直接记录程序终止，不调用模型生成 STOP，不进入 SFT |

## 三、日期如何处理

### 3.1 VERIFY 增加日期证据

具有非空日期的候选提供以下结构。SUPPORTED/CONFLICTED 必须具有原文支持的完整日级日期。

```json
{
  "event": {"time": "2013-07-27", "summary": "原文支持的具体事件"},
  "date_evidence": {
    "document_id": "1532",
    "quote": "原文中包含日期和事件的连续片段",
    "time_expression": "27 Jul 2013"
  }
}
```

校验顺序：

1. 日期证据文档必须来自当前返回文档，并属于该候选的 evidence_ids。
2. quote 必须在原文标题/片段中出现；time_expression 必须在 quote 中出现。允许空白归一化，不允许自由改写引文。
3. 日期表达须能解析为所填的日期和精度。支持 ISO、带年份的英文月份表达、中文年月日；不猜测相对日期、缺失年份和有歧义的数字日期。
4. `January 2011` 可以作为 `2011-01`，不能作为 `2011-01-01`。
5. 缺少日级证据时，VERIFY 使用 INSUFFICIENT，time 可以是原文支持的部分日期或 null；不进入正式日级时间线，也不会成为 gap 日期锚点。
6. 时间未知时规范化 date_evidence 为 null，避免残留一次错误回答中的日期证据。

第一阶段 memory 仍可以保存年月级或未知日期的暂定事件，供后续检索使用。正式时间线继续按日级接口导出，保持现有评测格式，不把未知日期塞进评测日期集合。

APPEND 保留日期证据；UPDATE 的日期只能来自两个输入事件之一，代码按最终采用的输入日期保留其证据，不能让合并模型重新编造出处。

新的校验函数默认开启严格证据检查，API VERIFY 也默认开启；新配置明确写入 `strict_date_evidence: true`。兼容开关不是正式实验的推荐设置。

### 3.2 日期元数据不充当事件边界

历史 `event-055` 的引用文档发表元数据是 `2011-02-03`，但返回片段明确含有 `27 Jul 2013`。新的校验接受正文支持的 `2013-07-27`，不因为它晚于元数据就拒绝。

检索仍支持 none/soft/hard 时间过滤，过滤字段仍然是文档发表日期，不是事件发生日期。第一阶段探索与第二阶段日期提示都明确：聚合页可描述更晚事件，回顾页可描述更早事件；元数据范围不用于证明事件已到终点。原有范围检索算法、索引和冻结快照未改动。

## 四、第一阶段 memory 与停止逻辑

流程现在为：

```text
SEARCH → MEMORY_UPDATE（原文暂定事件/日期/粗阶段）
       → VERIFY → MERGE
       → 程序回写本轮增益 → 下一次 SEARCH / STOP 决策
```

### 4.1 增益来自实际合并结果

每轮 search_history 新增：

- `new_verified_event_count` / `new_verified_event_ids`：通过 VERIFY 且被 MERGE 新加入时间线的事件。
- `evidence_update_count` / `evidence_updated_event_ids`：已有事件新增了不同文档的证据。
- `last_cycle_outcome`：当前最近一轮的上述结果摘要。

仅改写摘要、不新增事件或文档证据，不计作这些增益。返回 8 篇新文档、提取出 16 条暂定观察，也不自动等于时间线取得进展。这里的“verified”是流程中的模型验证状态，不是独立人工真值。

这些字段是在本轮 MERGE 后产生，下一轮 Controller 可以读取；不会泄漏未来检索结果或 Gold。若本轮新加入了正式事件，先把合并前生成的 skeleton_ready 清为 false，要求后续重新评估粗骨架是否足够。

轨迹额外记录 merge 后 memory 与事件状态，离线审计可以重放增益计算；这是程序状态转移，不增加一个需要模型学习的动作。

### 4.2 重复检索控制

- 保留原有精确 query 去重。
- 同一 strategy 已连续出现 3 次时，第四次要求转向其他策略。
- 对 query 做去常见词的 token 集合 Jaccard 比较，阈值为 0.6；明确不同年份的检索保留为不同时间段。
- 相近查询已经有两轮“新增事件为 0、证据增益为 0”，则不能继续做相近重复检索，应换事件、时期、角度，或记录暂缓原因。

这是一个可解释、无新增模型调用的词面近似检查，不是完整语义意图识别。它可能漏掉大幅同义改写，也可能误拦相近措辞；仍需真实试跑检查误判和纠错开销。

### 4.3 暂缓不等于完成

MEMORY_UPDATE 新增可选列表 `deferred_directions`，为空时返回 `[]`。每项包含 direction、简短 reason、实际 attempted_queries。程序要求至少两个不重复、可比较且已完成的无增益查询，校验后标为 `DEFERRED_UNCONFIRMED`。

模型只能把当前可行动的粗探索方向放入 next_search_directions，不需要每轮填满 8 项。多次查不到的方向可以明确暂缓，并继续作为不确定线索保留、跨阶段传递；不能写成“已解决”“事件不存在”或“世界时间线已经结束”。

自主 STOP 的基础门槛仍然存在：至少 6 轮、至少 4 个正式事件、至少 3 个粗阶段、尝试过 EARLIER 和 LATER、模型判断 skeleton_ready，且没有仍待行动的粗探索方向。详细因果缺口留给第二阶段。

程序仍不能独立证明模型给出的阶段划分完整，也不能证明暂缓理由在语义上充分；这部分需要抽查和后续偏好标注，不能只凭门槛就认定高质量轨迹。

## 五、第二阶段调度和 STOP

OPEN gap 的排序改为：尝试次数少者优先；次数相同则优先级高者优先；最后以 gap_id 确定稳定顺序。新 gap 或尚未尝试的 gap 会先获得机会。所有 gap 都尝试过后，未解决的 gap 仍可再次尝试。

没有把未解决 gap 强行改成 RESOLVED，也没有把一次无结果当作完成。原有 SEARCH 空字符串仍表示模型判断该 gap 无需搜索，并进入 NO_SEARCH_NEEDED。

到运行上限时，如果还存在 OPEN/IN_PROGRESS/FAILED，则产生 `forced=true` 的程序终止记录，来源标为 `deterministic_runner_limit_not_training_target`，不再为该 STOP 额外调用 API，也不导出成 SFT 正例。真正结束已选 gap 处理流程时，仍保留模型 STOP 路径。

新配置仍维持第一阶段最多 16 轮、第二阶段最多 4 轮，便于后续同上限比较。因此 8 个 gap 不保证在一次 4 轮运行中全部访问。调度公平解决的是“高优先级重复占用”，不是取消运行限制。

## 六、文件作用

| 文件（相对 chronos_repro） | 作用 |
| --- | --- |
| `src/chronos_repro/date_evidence.py` | 日期表达解析、原文引用与日期精度对齐 |
| `src/chronos_repro/full_timeline.py` | VERIFY 严格日期验证，APPEND/UPDATE 保留日期证据 |
| `scripts/run_full_timeline_api_agent.py` | 更新 VERIFY 输入输出和纠错提示，不再诱导补成月初 |
| `src/chronos_repro/exploration_control.py` | 相近 query 检查、merge 后增益计算、暂缓方向校验 |
| `src/chronos_repro/exploration_memory.py` | 接入新字段、暂定日期精度约束、探索提示及策略检查 |
| `scripts/exploration_phase.py` | 第一阶段实际执行增益回写，保存可重放状态 |
| `src/chronos_repro/tisa_rollout.py` | gap 按尝试次数与优先级公平调度 |
| `src/chronos_repro/gap_temporal.py` | 对模型说明聚合页日期元数据风险 |
| `scripts/run_tisa_two_phase_annotation.py` | 第二阶段强制停止不调用 API、不进入训练标签 |
| `scripts/analyze_exploration_rollout.py` | 在原有 memory 审计上增加 merge 后增益重放检查 |
| `scripts/validate_exploration_repairs.py` | 读取历史 Egypt 证据，输出独立的离线修复报告 |
| `tests/test_exploration_repairs.py` | 新增日期、retry、增益、暂缓和调度回归测试 |
| `configs/tisa_two_phase_temporal_v5_exploration_v2.json` | 下次试跑使用的新配置，旧配置和旧试跑产物保持不变 |
| `artifacts/exploration_repairs_v2/offline_validation.json` | 本轮历史证据复核、调度选择和文件哈希结果 |

同时更新已有 full_timeline、exploration_memory、gap_temporal 测试中的相关预期。

## 七、验证结果和复查命令

完整离线测试：137 passed。包含模拟模型的实际 runner 路径，以及使用本地 BM25 的第二阶段流程测试，不只是单函数检查。

历史证据复核：

- `event-001`：新校验拒绝原有 `2011-01-01`，因为原文只支持 `January 2011`。
- `event-055`：新校验接受正文中的 `2013-07-27`，不误用发表元数据上界。
- 同一历史最终 gap 状态：旧规则下一项是已尝试多次的 gap-008；新规则下一项是尚未尝试的 gap-004。这里只检验调度选择，没有伪造其检索结果。
- 旧轨迹 16 次 memory 更新重放仍通过，第二阶段继承 memory 一致。历史没有新增的 merge 增益字段，所以该历史报告的 post_merge_progress_checked 为 0；新路径的增益重放由离线集成测试验证。
- trajectory、prediction、evaluation 的哈希前后一致。没有改写旧分数，没有批量重标历史 SFT。

在 PowerShell 中运行：

```powershell
Set-Location D:\paper\chronos_repro
& D:\miniforge\envs\tls\python.exe -m pytest tests -q
& D:\miniforge\envs\tls\python.exe scripts/validate_exploration_repairs.py --run-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v1 --output artifacts/exploration_repairs_v2/offline_validation.json
```

测试必须从 chronos_repro 目录运行：本轮首次从 D:\paper 根目录运行，因部分既有测试导入 scripts 而收集失败；切换至正确项目目录后解决，没有为此安装额外依赖。

## 八、下一步与边界

下一步先用新配置做真实小规模 smoke，检查模型是否遵循日期证据协议、是否频繁触发 query 误拦和纠错，再进行完整轨迹及相同检索上限比较。本轮只准备入口，未执行以下联网命令：

```powershell
Set-Location D:\paper\chronos_repro
& D:\miniforge\envs\tls\python.exe scripts/run_tisa_two_phase_annotation.py --project-root . --config configs/tisa_two_phase_temporal_v5_exploration_v2.json --env-file .env --output-dir artifacts/tisa_two_phase_temporal_egypt_v5_exploration_v2
```

后续仍使用已有 .env，密钥不写入文档；余额不足立即停止，其他可修复错误继续按既有规则处理。新目录尚不代表已有试跑结果。

需重点观察：严格日期约束可能降低短期召回；不支持的时间表达会保留为未知而不是猜测；词面意图规则并非语义判别；新增文档证据也不必然代表新的事实；阶段完整性与停止正确性需要独立语义抽查。本轮未完成提示词压缩、成本消融或新的覆盖评测，旧轨迹仍不能直接认定为高质量训练数据。

保持原研究约束：不注入 Egypt 专用 task_description，继续依赖冻结 keywords、通用无关 few-shot 与真实文档发散；学生状态不加入 budget；Gold 不进入本轮实际策略输入。
