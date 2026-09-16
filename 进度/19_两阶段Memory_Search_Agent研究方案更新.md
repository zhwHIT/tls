# 两阶段 Memory Search Agent 研究方案更新

> 日期：2026-09-07  
> 状态：完成可行性分析、相关工作边界、数据规模审计和研究方案更新；本轮未调用 DeepSeek API。

## 1. 本轮动机

把时间线 Search Agent 的研究重点调整为两个连续阶段：

1. 骨架探索：从空时间线开始，以时间覆盖、事件类型和锚点发现为目标持续检索；
2. 缺口精修：基于已有时间线分析因果断点、缺失要素和证据冲突，定向检索并决定停止。

同时判断哪些动作必须训练、现有数据是否足够，以及 Gold 如何用于第二阶段 DPO。

## 2. 最终方案

完整轨迹保留 `SEARCH / VERIFY / MERGE / STOP`，但第一版只训练检索 Controller：

- Controller 学习短结构化思考、Memory update、query、SEARCH/STOP 和 SWITCH_PHASE；
- VERIFY 使用冻结通用模型，并由程序校验证据 ID、日期与支持关系；
- MERGE 使用冻结通用模型选择 APPEND/UPDATE/DROP，UPDATE 另行融合，再由程序校验；
- Writer 只读取已验证事件。

这使毕业设计仍是完整多动作 Agent，同时把训练集中在最影响覆盖率、成本和过早停止的部分。

## 3. 可行性与已有工作

- R2A-TLS 已证明初始检索、缺口反思和深检索对 TLS 可行；
- Amber、MemSearcher 已证明 compact Memory 可驱动迭代 query 与停止；
- Query DPO 已证明可按检索排序信号优化 query；
- DPO 适合同状态局部偏好，DMPO 提醒普通 DPO 不宜直接套完整多轮轨迹；
- 因此两阶段流程作为可靠骨架，毕业设计的组合增量为 TLS 双 Memory、Gold 局部偏好、阶段切换学习和模块化执行器。

## 4. 数据量审计

| 项目 | 当前数量 |
|---|---:|
| 主题 | 60 |
| 对齐 Gold 事件 | 4,090 |
| Gold 任务模板 | 120 |
| train/dev/test | 90/14/16 |
| train 独立主题 | 43 |
| accepted 教师标注 | 1 |
| 已编译 Tool-SFT / DPO / RL | 1 / 4 / 1 |

当前数据足够验证流程，不足以证明泛化。主实验先从每个 train topic 的不同前缀、遮蔽和预算派生 5–10 个 rollout，目标 1k–3k 局部偏好对；6k–12k query 对和 2k–4k SEARCH/STOP 对作为扩展目标，而不是毕业硬门槛。

## 5. Gold 标注重点

第二阶段使用 private Gold 删除事件或字段以生成 gap state。候选 query 必须在同一冻结检索器中真实回放，再按边际 Gold 覆盖、证据支持、冗余和成本生成：

- query chosen/rejected；
- SEARCH 与 STOP 正负例；
- SWITCH_PHASE 与继续粗搜正负例；
- Memory update 正负例。

Gold 不进入学生 prompt，dev/test Gold 只用于离线评测。

## 6. 更新文件

- `时间线_Search_Agent_框架研究方案.md`：新增两阶段架构、双 Memory、模块分工、Gold DPO、数据规模、消融和八周路线。
- `report-source.md`：新增一手证据账本、研究边界、数据审计和最小消融集合。

## 7. 下一步

先实现双 Memory schema 和阶段状态机，再用现有冻结语料完成 20–50 条 pilot rollout。pilot 通过后只扩充 Controller 所需的 query、阶段切换和 SEARCH/STOP 偏好；暂不训练 VERIFY/MERGE。
