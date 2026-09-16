# 三主题诊断预检结果与 API 实测边界

日期：2026-09-14。本文补充并更新第 39 号文档的进展状态。

## 当前结论

首轮两阶段状态接口已接入主流程，最终离线回归为 **183 passed in 10.22s**。Yemen、MJ、David Bowie 三个主题的冻结快照、索引绑定和片段读取预检全部通过。**没有执行新版 DeepSeek 轨迹，没有新的 Gold 召回成绩，本轮 API 调用为 0。**

第一阶段保留按时间排序的精简事件目录、阶段摘要、边界与探索方向；第二阶段 SEARCH 和补缺后 GAP_MEMORY 更新使用局部相关事件，初始化 GAP_MEMORY 仍使用全局目录。局部输入不是删除外部事件库。MEMORY_UPDATE 的近期查询、待核实线索和证据 ID 已补充重复投影兼容。主流程 trajectory/manifest 的版本标记改为读取配置，避免新版产物仍被标为 coverage-v6。

## 主题划分警告

只读核对发现 `configs/search_agent_topic_split_v1.json` 把 Yemen 设为旧 seed 的 test，而历史检索调试已观察过该主题。因此不能把本轮三个主题简单称为新的 dev 划分或干净 test 集。

正式使用的本轮计划改为 `chronos_repro/configs/tisa_multitopic_v7_diagnostic.json`，角色为 **diagnostic_only_previously_inspected**。不得用于训练、checkpoint 选择或声称未见测试泛化。旧 seed 配置保持不变。第 39 号文档中最初的 dev 计划仅为准备阶段记录，不作为此次正式执行入口。

## 已完成的本地预检

| 主题 | 冻结文档数 | 快照 ID | 检索结果 / 选中片段 | 首次检索计时 |
|---|---:|---|---|---:|
| Crisis / yemen | 4,046 | ece08f344cc94933 | 40 / 8 | 163.043 秒 |
| T17 / mj | 121 | 2704b6b058774e15 | 40 / 8 | 53.775 秒 |
| Entities / David_Bowie | 889 | 25ac73e52bc93b3b | 40 / 8 | 80.856 秒 |

计时包括预检中的首次 search 和片段访问，尚未拆分模型初始化、索引加载与实际检索耗时，不能据此断言稳态检索性能。

报告分别位于：

- `chronos_repro/artifacts/tisa_v7_diagnostic/crisis_yemen/preflight.json`
- `chronos_repro/artifacts/tisa_v7_diagnostic/t17_mj/preflight.json`
- `chronos_repro/artifacts/tisa_v7_diagnostic/entities_David_Bowie/preflight.json`

每个目录另有 `run_config.json`、`run_binding.json`。绑定包含配置、索引 manifest、dense manifest 和快照 manifest 的哈希。旧轨迹、旧账本和冻结语料均未覆盖。

三个新配置位于：

- `chronos_repro/configs/tisa_v7_crisis_yemen.json`
- `chronos_repro/configs/tisa_v7_t17_mj.json`
- `chronos_repro/configs/tisa_v7_entities_David_Bowie.json`

三者沿用同一检索与执行参数，启用 `compact_context.enabled=true`。本轮尚未更换检索后端或实现直接片段向量索引对照。

## 尚未获得的证据

- 没有新版完整两阶段轨迹，不能声称严格日期召回达到 70%。
- 没有事件语义覆盖或正确性的独立新结论。
- 字符上限 64,000 不是 8K token 上限；目标 Qwen 型号和 tokenizer 仍需确认，训练显存尚未实测。
- 9 项新增单元测试与额外离线投影检查已通过；进一步的客户端包装专项测试文件创建被审批服务容量错误拒绝，不能计入测试数量。

## API 实测前需要确认的范围

预检不会读取 API key。本次传入的 `D:\paper\.env` 经预检显示不存在；检查其他候选路径的操作被审批服务容量错误阻断。后续需要确认已有 `.env` 的实际路径，**无需再发送密钥，也不要覆盖已有密钥文件**。

如沿用当前三个配置，待确认的执行范围为：

- 端点：`https://api.deepseek.com`；模型：`deepseek-v4-flash`。
- 主题：上述三个冻结主题；不发送 Gold 或私有参考标签。
- 每主题最多 28 次搜索（第一阶段 16 次、第二阶段 12 次），每次最多 8 个片段。
- 每片段正文最多 3,200 字符、前文最多 400 字符，另含标题、ID、查询、候选事实、memory、时间线与引用证据。
- 每主题最多 400 次累计 HTTP 请求，三个主题合计最多 1,200 次；包括重试与重启计数。该数量不是金额预算，也不是预期实际调用量。
- 片段和派生内容可能在提取、校验和重试中重复发送；片段槽位不是总传输字节上限。
- 任一主题出现余额不足，立即停止该主题，并停止启动剩余主题。禁止重置旧余额停止记录来绕过限制。
- 这是较历史短片段检索相关性标注更大的发送范围，配置文件本身不代表已授权启动。

## 下一步

确认已有密钥文件路径及新调用范围后，先进行首主题的短程真实输入检查，再按相同配置继续三主题完整诊断。阶段安全上限强制结束与模型自主 STOP 必须分别报告。多主题指标使用严格日期交集，逐主题报告，再给出宏平均和微平均；若有主题缺失或运行失败，不能静默排除后宣布整体达标。

离线汇总命令，在 `D:\paper\chronos_repro` 执行：

```powershell
& 'D:\miniforge\envs\tls\python.exe' -B scripts/evaluate_multitopic_timeline.py --project-root D:\paper\chronos_repro --suite configs/tisa_multitopic_v7_diagnostic.json --output artifacts/tisa_v7_diagnostic_report_01.json
```

评测脚本不调用模型，拒绝覆盖已有报告。尚无轨迹时输出缺失状态，而不是伪造性能结果。
