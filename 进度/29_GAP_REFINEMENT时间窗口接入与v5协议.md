# GAP_REFINEMENT 时间窗口接入与 v5 协议

> 日期：2026-09-11
> 状态：框架接入、prompt、校验、Memory、SFT 导出及本地测试已完成；全量 97 passed。
> 本轮未调用外部 LLM。下文动作示例是接口说明，不是新生成的 DeepSeek 轨迹。

> 后续状态：已完成真实 API pilot，结果及修复见 [30_Egypt_v5真实轨迹验证与错误修复](30_Egypt_v5真实轨迹验证与错误修复.md)。本文件保留接入阶段记录。

## 1. 本轮动机与实现范围

上一轮统一 qrel 实验显示，soft 的 Recall@20 为 0.8700，优于 none 的 0.8072。但该实验以 Gold 日期作为窗口中心。要验证真实 Agent，必须让 Controller 使用可观察的事件序列和 Memory 选择时间范围。

本轮新增可运行的 v5 配置，把日期窗口贯通到 run_tisa_two_phase_annotation.py 的第二阶段。第一阶段仍全局探索，不施加日期约束。原 v4 配置仍使用旧行为，可复现教师监督标注；新 v5 默认 phase2_teacher_guidance=false，第二阶段也只基于公开状态决策。

## 2. 模型输入

原输入保留 dataset、topic、phase、有序 events、memory、active_gap、valid_actions，不加入预算字段。

v5 新增 retrieval_contract，说明实际过滤字段为文档发布日期，列出可选模式、可见日期锚点、建议扩展天数和校验规则。每个 gap 的 temporal_context 由程序根据 left_event_id/right_event_id 在当前时间线中的日期生成。更新时间线后重新计算，避免沿用旧日期。

示例：当前 e1 日期为 2020-01-01，e2 日期为 2020-02-01，gap 两侧为这两个事件。

~~~json
{
  "temporal_context": {
    "anchor_event_ids": ["e1", "e2"],
    "date_from": "2020-01-01",
    "date_to": "2020-02-01",
    "source": "current_timeline_neighbors"
  }
}
~~~

Controller 可以引用当前 events 中其他有效事件作为窗口锚点，不限于 gap 的两个邻居。没有合适锚点时选择 none。初版采用可验证的“可见事件日期 + 扩展天数”约束，没有实现从原始正文任意抽取日期作为窗口来源。

## 3. 模型输出与 prompt

SEARCH 保留简短 thought、gap_id 和 query，新增 date_filter：

~~~json
{
  "thought": "补充两次事件之间的转变过程，同时保留稍后回顾性报道。",
  "action": "SEARCH",
  "gap_id": "g1",
  "query": "Egypt transition government resignation",
  "date_filter": {
    "mode": "soft",
    "date_from": "2019-12-18",
    "date_to": "2020-02-15",
    "anchor_event_ids": ["e1", "e2"],
    "padding_days": 14
  }
}
~~~

新增 prompt 要求：

1. 窗口只引用当前 events 与 Memory；锚点 ID 必须可见。
2. date_from 等于最早锚点日期减 padding_days，date_to 等于最晚锚点日期加 padding_days。
3. 优先尝试 soft，以保留回顾性证据；没有有效锚点或此前局部检索收益不足时可选择 none。
4. hard 需要至少两个不同日期的可见锚点，且应有明确局部检索理由。
5. query 为空时使用 none，日期为 null、锚点为空列表、padding_days 为 0。
6. 检查历史 query 和范围，避免无新依据地重复同一次检索。

程序能验证锚点与日期的一致性，不能仅靠 schema 证明检索意图合理；后者仍需轨迹评测和 SFT/DPO 学习。默认允许 0–90 天扩展，14 天只作为建议，并不是由上一轮实验证明的最优值。

## 4. 公开策略与私有教师边界

新配置 phase2_teacher_guidance=false 时，GAP_MEMORY 初始化、gap SEARCH 与 MERGE 后的 GAP_MEMORY 更新请求中均移除 teacher_only_reference_events、teacher_only_target_events 和 teacher_alignment 输出要求，使用公开策略 system prompt。

main 仍读取 Gold 用于最终本地覆盖评测和独立私有文件；它不进入上述策略 API 请求。VERIFY/MERGE 沿用证据驱动接口。

保留 phase2_teacher_guidance=true 可继续旧教师数据构造流程。两种来源写入不同 label_source，v5 轨迹元数据不会把纯策略执行误记为私有参考监督。

## 5. 执行与 Memory 闭环

非空查询路径：

SEARCH 决策 → 日期校验/修正重试 → Retriever → VERIFY → MERGE → 更新 GAP_MEMORY → 下一轮。

有效 date_filter 被转成 retrieval.py 接口的 date_from、date_to、date_filter_mode、date_soft_penalty，适用于 BM25 与 Hybrid。每次动作保存实际检索参数、完整返回排名及其日期审计信息。

Memory 的每个 gap 新增 search_attempts，全局增加 search_history，记录 query、date_filter、实际参数、返回文档 ID、数量和是否跳过。新出现的 gap 仍由原 GAP_MEMORY 更新逻辑登记，下一轮会根据更新后的事件序列重新生成 temporal_context。

空查询保留原来的跳过 VERIFY/MERGE 记录和 NO_SEARCH_NEEDED 状态，并记录未执行检索。当前步骤输入使用独立副本，避免把本步刚生成的 query 或检索结果写回本步输入，造成 SFT 未来信息泄漏。

## 6. 校验与重试

拒绝无效日期格式、逆序边界、未知锚点、重复锚点、非法 padding、与锚点不一致的日期、单日期 hard、空 query 携带过滤窗口等情况。

无效输出进入现有 repaired_call，错误反馈要求模型重新返回符合 schema 的动作。同步修复了 schema 校验失败响应的用量遗漏：已返回但未通过校验的模型响应仍计入用量。余额不足异常继续直接抛出，不进入格式重试。

## 7. SFT 与文件版本

含日期契约的输入标记 schema_version=5；编译出的 assistant 动作保留 date_filter，system prompt 包含日期规则。主运行输出使用 v5 轨迹和 sft_v5.jsonl；没有开启 temporal_search 的旧配置仍输出 v4 文件。

训练输入仍不包含预算。当前编译器沿用 train 数据标识，新配置使用 Egypt train 主题；本轮没有把 dev qrel 编译成训练样本。完整终止策略和 STOP 偏好标注仍需后续实验，执行器的循环上限不能作为“任务已完整覆盖”的证据。

## 8. 新增和修改的文件

| 文件（相对 chronos_repro） | 作用 |
|---|---|
| src/chronos_repro/gap_temporal.py | 可见锚点、日期动作 schema/prompt、程序校验、参数映射与 Memory 历史 |
| scripts/run_tisa_two_phase_annotation.py | 第二阶段实际调用、公开策略分支、修正重试、轨迹和 SFT 贯通 |
| configs/tisa_two_phase_temporal_v5.json | Egypt、全量 v3 混合索引、公开策略、可见锚点日期窗口配置 |
| tests/test_gap_temporal.py | 日期边界及真实 BM25 的离线集成测试 |

## 9. 验证结果与限制

定向测试 16 passed；全量测试 97 passed；Python 编译与配置 JSON 校验通过。本轮新增 12 个测试用例，包括参数化无效窗口测试。

集成测试使用确定性的本地模型响应和真实 BM25 索引，验证：错误日期修正后才执行检索；hard 只返回范围内文档；私有事件哨兵和私有字段不出现在策略请求中；新增 gap、空 query、Memory 历史和 SFT 输出保持一致；错误响应用量被累计。模型测试替身不代表真实 DeepSeek 的策略质量。

本轮尚未执行 v5 的真实 API 轨迹，也没有新 token 开销。新配置仍是短程 pilot（第一阶段最多 2 轮、第二阶段最多 4 个 gap 周期），不能承诺覆盖全部 Gold。下一步以真实公开策略 pilot 检验模型是否能正确引用锚点和生成窗口，再做相同候选深度的检索对照与 train 轨迹扩充。

## 10. 运行入口

以下命令会真实调用 API，配置和流程已准备就绪；本轮只执行本地测试。

~~~powershell
cd D:\paper\chronos_repro
conda activate tls
$env:PYTHONPATH = 'src'
python scripts\run_tisa_two_phase_annotation.py --project-root . --config configs\tisa_two_phase_temporal_v5.json --env-file .env --output-dir artifacts\tisa_two_phase_temporal_egypt_v5
~~~

本轮不需要安装依赖；后续如需 pip 安装，继续使用清华源。
