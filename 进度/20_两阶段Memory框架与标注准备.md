# 两阶段 Memory 框架与标注准备

> 日期：2026-09-07  
> 环境：Conda `tls`  
> 状态：本地框架、数据任务、离线测试和 1 条 DeepSeek Phase 2 pilot 均已完成。

## 1. 本轮目标

根据更新后的研究方案，把原有单缺口四动作标注链扩展为：

```text
SKELETON_EXPLORATION
  → Memory-conditioned SEARCH / SWITCH_PHASE
  → frozen VERIFY / MERGE
  → GAP_REFINEMENT
  → Memory-conditioned SEARCH / STOP
```

完整运行轨迹仍为 `SEARCH / VERIFY / MERGE / STOP`，但训练重点是 Controller 的 QUERY、ACTION 和 MEMORY 三类局部决策。

## 2. 已实现代码

### `src/chronos_repro/tisa_memory.py`

- 定义 `SKELETON_EXPLORATION`、`GAP_REFINEMENT`；
- 构造 Coverage Memory 和 Gap Memory；
- 校验阶段与合法动作；
- 从 private Gold 提取允许日期；
- 按 exact Gold gain、两日窗口 gain、新日期、文档数和 query novelty 排序候选 query；
- 校验候选 query、Controller 动作和 QUERY/ACTION/MEMORY 偏好 margin。

### `scripts/prepare_tisa_memory_tasks.py`

- 从冻结 Crisis、T17、Entities 快照重新生成任务；
- 阶段一为每个 dataset-topic 生成 empty、head、sparse 三种状态；
- 阶段二继续生成 MISSING_DATE、CAUSAL_BRIDGE、MISSING_ROLE、EVIDENCE_CONFLICT；
- policy state 和 private target 分文件；
- 保持 topic-disjoint 与跨数据集语义组隔离；
- 写入输入配置、快照和索引 SHA-256。

### `scripts/annotate_tisa_memory_train.py`

- 只接受同一 `train` 目录中的 policy/private 成对文件；
- 第一次教师调用生成检索前结构化 thought、Memory draft 和 3–4 条 query；
- 在冻结索引中真实执行全部 query；
- 程序用 private Gold 计算边际覆盖并排序；
- 第二次教师调用生成 chosen/rejected QUERY、ACTION、MEMORY；
- JSON、query 长度、Gold 抄写、真实回放、阶段动作和偏好 margin 失败时定向修复；
- 网络/限流/服务错误由客户端重试，余额不足立即停止；
- 支持 task-id、phase、limit 和 resume。

### `scripts/compile_tisa_memory_training_data.py`

每条 accepted Controller 标注编译为：

- 1 条 Controller tool-SFT；
- 3 条同状态局部 DPO：QUERY、ACTION、MEMORY；
- 1 条可回放 Controller episode。

VERIFY/MERGE 标记为 frozen executor，不进入 v3 Controller DPO。

## 3. v3 数据任务结果

| split | 总任务 | 骨架探索 | 缺口精修 |
|---|---:|---:|---:|
| train | 225 | 135 | 90 |
| dev | 35 | 21 | 14 |
| test | 40 | 24 | 16 |
| 总计 | 300 | 180 | 120 |

train 包含 43 个规范化主题名、45 个 dataset-topic 实例。数据生成连续运行两次，manifest SHA-256 完全相同。

## 4. 自动验证

- Python 语法检查通过；
- 50 个 pytest 测试全部通过；
- 新增 Memory/action 约束测试；
- 新增 Gold query 排序测试；
- 新增 Controller SFT/三类 DPO 编译测试；
- 新增 FakeClient 双教师调用端到端测试，全程不访问网络。

## 5. 外部 LLM pilot 结果

用户随后明确授权指定数据范围，已完成：

`crisis:egypt:gold-3b8c742b542f:missing_date:phase2`

- API 状态：成功，未出现余额不足；
- accepted / rejected：1 / 0；
- chosen action：SEARCH；
- rejected action：STOP；
- chosen query：`Egypt new parliament inaugural session date January 2012`；
- exact Gold gain：1；
- 两日窗口 Gold gain：3；
- QUERY chosen/rejected：0.9 / 0.2；
- ACTION margin：0.85；
- MEMORY margin：0.70。

proposal 首次返回的 query 数量不满足 3–4 条约束，定向修复一次后成功；review 一次成功。两阶段审计记录只保存模型名、usage、attempts、request ID 和响应 SHA-256，不保存密钥。

成功的第二次 proposal 记录为 2,133 tokens，review 记录为 15,406 tokens；首次校验失败 proposal 的 usage 在旧审计逻辑中未保存，因此二者之和不是本次完整总成本。现已修复审计器：以后即使标签校验失败，也保留该次响应的 usage 和 SHA-256。主要开销来自 4 条 query、每条 12 篇、每篇最多 500 字符的 review 输入。已将后续外发配置压缩为每条 query 前 4 篇、每篇最多 300 字符；完整 top-12 检索结果仍只保存在本地用于程序评分与回放。

## 6. v3 编译结果

已生成：

- 1 条 `tisa_memory_controller_sft_v3.jsonl`；
- 3 条 `tisa_memory_controller_dpo_v3.jsonl`，分别为 QUERY、ACTION、MEMORY；
- 1 条 `tisa_memory_controller_episodes_v3.jsonl`；
- 所有文件均写入 SHA-256，JSONL 解析与 private key 扫描通过。

## 7. 新增或更新文件

- `chronos_repro/src/chronos_repro/tisa_memory.py`
- `chronos_repro/src/chronos_repro/tisa_data.py`
- `chronos_repro/scripts/prepare_tisa_memory_tasks.py`
- `chronos_repro/scripts/annotate_tisa_memory_train.py`
- `chronos_repro/scripts/compile_tisa_memory_training_data.py`
- `chronos_repro/configs/tisa_memory_supervision_v3.json`
- `chronos_repro/configs/tisa_memory_annotation_v3.json`
- `chronos_repro/tests/test_tisa_memory.py`
- `chronos_repro/tests/test_tisa_memory_annotation.py`
- `chronos_repro/tests/test_tisa_memory_compiler.py`
- `chronos_repro/README.md`
- `chronos_repro/artifacts/tisa_memory_tasks_v3/`
- `chronos_repro/artifacts/tisa_memory_teacher_pilot_v3/`
- `chronos_repro/artifacts/tisa_memory_training_pilot_v3/`

## 8. 下一步

当前 Phase 2 数据链已贯通。下一步先审阅这条完整 Controller SFT/DPO 内容，再单独申请并执行 1 条 Phase 1 empty pilot，验证 Coverage Memory 和 `SEARCH > SWITCH_PHASE`；暂不直接扩到 225 条。
