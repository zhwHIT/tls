# Egypt v5 真实轨迹验证与错误修复

> 日期：2026-09-11
> 实际 API pilot 已完成，status=ok；25 步、19 个事件、25 条原始 SFT 记录。
> 这是短程流程验收，不是完整 Gold 覆盖验收；仍有 2 个开放 gap。

> 后续变更：用户否决专项 task_description 方案，该配置已撤回；改为文档驱动 memory 与通用 few-shot，见 [31_第一阶段文档驱动Memory与自主发散检索](31_第一阶段文档驱动Memory与自主发散检索.md)。本文保留当时的试跑与修复记录，不代表当前配置。

## 1. 本轮执行与动机

上一阶段完成了 v5 时间动作的离线接入。本轮在用户要求继续后，使用 tls 环境、.env 中的既有密钥与 deepseek-v4-flash，执行 Egypt train 主题真实两阶段流程。检索使用冻结 Crisis v3 混合索引，每轮 Top-8，每篇最多 700 字符。

第一阶段最多两轮 SEARCH；第二阶段最多四个 gap 周期。第二阶段不向策略提供私有参考事件，窗口依据当前可见事件日期生成。Gold 只用于本地最终评测与独立私有文件。

运行前增加按请求内容哈希的成功响应缓存，使相同请求恢复时可复用结果。本轮实际未发生重启或缓存命中，40 个成功响应均来自 API；余额不足保护始终保留，未触发余额不足。

## 2. 实际动作与检索范围

第一阶段产生 5 个事件，第二阶段将时间线扩展至 19 个事件。六次 SEARCH 都为非空查询，共返回 48 个文档位置、43 篇不同文档。

| 阶段/步骤 | 实际 query | 模式与发布日期范围 | 本轮新文档 |
|---|---|---|---:|
| 骨架 step-001 | history of Egypt timeline major periods milestones | none | 8 |
| 骨架 step-004 | ancient Egypt major periods timeline Old Kingdom New Kingdom | none | 4 |
| gap step-009 | Hosni Mubarak resignation February 11 2011 | soft；2011-01-11 至 2011-02-25 | 8 |
| gap step-013 | Egypt presidential runoff Morsi Shafiq June 2012 result | soft；2012-05-09 至 2012-07-08 | 7 |
| gap step-017 | Egypt interim military rule parliamentary elections transition 2011 2012 | soft；2011-01-28 至 2012-06-06 | 8 |
| gap step-021 | Egypt parliamentary elections results new parliament January 2012 | soft；2011-11-14 至 2012-02-20 | 8 |

四次日期动作均选择 soft 和 padding_days=14，均首次通过日期 schema 校验。它们引用的是当前 events 中的 ID；最后一轮引用新加入的 event-014、event-016，验证了新增事件可以成为下一轮检索锚点。

第三轮区间跨越一年多，因为模型选择的两侧锚点相距较远；当前协议是“锚点最小/最大日期 + 两侧扩展”，不是对单个中心日期一律做 ±14 天。

实际步骤序列：

~~~text
SKELETON_EXPLORATION:
SEARCH -> VERIFY -> MERGE -> SEARCH -> VERIFY -> MERGE -> STOP

GAP_REFINEMENT:
GAP_MEMORY
-> SEARCH -> VERIFY -> MERGE -> GAP_MEMORY
-> SEARCH -> VERIFY -> MERGE -> GAP_MEMORY
-> SEARCH -> VERIFY -> MERGE -> GAP_MEMORY
-> SEARCH -> VERIFY -> MERGE -> GAP_MEMORY
-> STOP
~~~

完整输入、输出、简短 thought、工具结果和 Memory 均在 trajectory.json 中。本轮没有触发空 query 分支；该分支此前已通过本地测试。

## 3. 时间线、gap 与覆盖结果

- 最终 19 个事件、17 个不同日期；范围为 2011-01-25 至 2012-06-24。
- GAP_MEMORY 最终包含 7 个 gap，其中 5 个被模型标记 RESOLVED、2 个仍 OPEN。该状态是模型判断，不等价于人工确认因果链已完整。
- 开放 gap-005：2 月 5 日至穆巴拉克辞职之间的局势升级、抗议或军方压力。
- 开放 gap-006：军方接管后的最初几个月与后续过渡安排。
- 最后 STOP 由固定执行周期上限触发，并非模型自主认定全局任务完成。

| 指标 | 结果 |
|---|---:|
| Gold 对齐事件数 | 201 |
| 当前匹配到的 Gold 事件 | 1 |
| Gold event recall | 0.50% |
| Gold 不同日期数 | 122 |
| 覆盖 Gold 日期 | 12 |
| Gold date recall | 9.84% |
| 日期 Precision | 70.59% |
| 日期 F1 | 17.27% |

当前 evaluate_gold_coverage 要求日期属于 accepted_dates，并达到 text_similarity 阈值 0.2；它是日期约束的文本相似度代理，不是人工语义完整覆盖判断。这可以解释部分事件匹配偏低，但不能掩盖真实遗漏：预测未覆盖 2012 年下半年及 2013 年，而 Gold 包含这些阶段。

因此本轮验证了日期 Controller 的实际可执行性、Memory 更新和证据检索闭环；没有实现完整时间线覆盖目标，也没有证明相对无日期 Agent 的最终质量提升。

## 4. 实际错误与原因

### 4.1 初始检索偏向古代史

两次骨架查询分别是泛埃及历史和古代埃及。初始紧凑输入主要给模型 topic=egypt，缺少明确的“埃及政治危机”任务范围，prompt 又要求 broad chronology。这使模型给出历史通览查询。冻结语料虽然仍返回了部分现代新闻，但 query 意图已偏离任务。

已修复：为后续 v5 配置加入公开 task_description，明确政治危机、抗议、政府更替、军方过渡和选举；通过 compact_state 传入实际策略请求，也记录到 phase1_visible/SFT 输入。没有从 Gold 注入具体日期或事件答案。

### 4.2 thought 超长反复触发校验

运行记录中有 15 次校验失败，原因全部为 thought must contain 6-240 characters。旧 prompt 只要求 brief，模型仍经常把较长证据分析放到 thought；已有修正机制随后成功缩短输出。

已修复：通用执行器、公开策略和教师 prompt 均明确规定 thought 为 6–240 字符，优先一句话；详细证据说明放在候选 reason 字段。保留校验和修正重试，未放宽长度规则。

### 4.3 被拒绝响应用量漏记

通用 SEARCH/STOP、VERIFY、MERGE、UPDATE 的旧循环在 schema 校验失败时只保存错误，丢弃该响应的 usage。完整响应缓存揭示了差异：步骤汇总为 176,766 tokens，而全部 40 个成功响应累计为 281,775 tokens，相差 105,009 tokens。

已修复：四条路径在记录校验错误前保留已返回的 audit，使之后成功时的用量累计包含被修正响应。新增回归用例验证“长 thought 响应失败 + 修正响应成功”两个响应都计入开销。

本轮原始 manifest 的 usage 保留当时统计，避免改写历史；实际更完整的本轮响应开销以 manifest.api_cache.all_cached_response_usage 为准。

## 5. 本轮调用开销

- 成功 API 响应：40；缓存命中：0。
- prompt_tokens：108,768。
- completion_tokens：173,007。
- total_tokens：281,775。
- 校验失败记录：15；均已在当次流程内修正。
- 余额不足：未出现。

上述为响应缓存记录的服务商返回用量，不是另行查询的账单或账户余额。修复 prompt 后未再次运行真实 API，因此不声称重试率或成本已经实测下降。

## 6. 验证结果

scripts/audit_temporal_rollout.py 已使用本地冻结索引重放六次检索：6/6 排名和文档 ID 顺序一致。四次日期动作可由各自步骤的可见事件重新校验；全部学生状态有序且无预算字段；SFT 的 user/assistant 内容与原步骤一致；最终事件引用的文档 ID 均来自此前检索结果。

证据 ID 可追溯不代表文本事实已获人工核实，尤其日期推断、同日重复事件、模型关闭 gap 的合理性仍需抽样检查。

修复后全量测试：100 passed。新增缓存余额终止、恢复不重复计费、VERIFY 修正响应用量计入等测试通过。本轮三模式实验中只实际使用了 soft；不能由此声称 hard/none 选择策略已经学会。

## 7. 产物与版本

运行目录：chronos_repro/artifacts/tisa_two_phase_temporal_egypt_v5。

| 文件 | 作用 |
|---|---|
| trajectory.json | 25 步真实轨迹，包括模型状态、动作、thought、工具观察和最终 Memory |
| prediction.json | 最终时间线预测 |
| evaluation.json | 日期分数与当前 Gold 文本相似度覆盖代理 |
| sft_v5.jsonl | 25 条原始格式样本；尚未完成训练质量筛选 |
| manifest.json | 运行完成状态、产物哈希、步骤统计与完整响应缓存用量 |
| run_config.json | 本次实际使用配置；SHA-256 与原 manifest 的 config_sha256 一致 |
| teacher_alignment.private.json | 仅本地评价相关的私有信息，不进入学生训练输入 |
| api_cache/ | 请求哈希与成功响应，供同请求恢复；不含 API key |
| audit.json | 六次检索重放、日期校验、SFT 一致性与开放 gap 检查 |

configs/tisa_two_phase_temporal_v5.json 已在运行结束后补充任务范围，用于后续运行；与本次使用的 run_config.json 不同。prompt 长度和成本修复也发生在本次运行之后，不能把当前代码配置下尚未进行的试跑当成本次结果。

可使用历史配置重做本地检索审计：

~~~powershell
cd D:\paper\chronos_repro
conda activate tls
$env:PYTHONPATH = 'src'
python scripts\audit_temporal_rollout.py --run-dir artifacts\tisa_two_phase_temporal_egypt_v5 --config artifacts\tisa_two_phase_temporal_egypt_v5\run_config.json --output artifacts\tisa_two_phase_temporal_egypt_v5\audit.json
~~~

## 8. 下一步优先级

1. 使用修正任务描述和明确 thought 限制的新配置开展下一条公开策略 pilot，使用新输出目录，保留本次轨迹。
2. 扩大骨架探索的有效覆盖；通过公开任务范围、语料日期范围和当前 Memory 发现终点遗漏，不用 Gold 直接给策略指定答案。
3. 区分真实自主 STOP 和执行器截断。当前 25 条记录是原始轨迹格式产物，两个阶段末尾的受限停止不应直接当“覆盖完成”的正例训练。
4. 逐项分析未解决 gap、同日重复、证据日期推断和 VERIFY 双过滤，之后再扩充 train 主题及构造 DPO。
5. 比较相同候选深度下有无日期偏置，区分时间约束收益与候选池扩大的收益；以最终时间线质量而非仅工具可执行性决定训练方案。
