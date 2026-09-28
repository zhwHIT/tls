# LLM-TLS 全数据集事件摘要提取

记录时间：2026-09-18。状态：已授权启动，尚未全量完成。

用户明确授权参考 LLM-TLS 代码，调用 API 对 T17、Entities 等数据集提取事件摘要和时间。
本次覆盖本地全部 T17、Entities、Crisis 冻结文章；只进行离线提取，未改动检索和 VERIFY。
按用户最新选择，不加入逐条事实核验 API；生成结果统一标为 `not_fact_checked`。

## 输入范围

| 数据集 | 快照 | 主题 | 文章数（既有原文索引） |
|---|---|---:|---:|
| T17 | `2704b6b058774e15` | 9 | 4,203 |
| Entities | `25ac73e52bc93b3b` | 47 | 51,183 |
| Crisis | `ece08f344cc94933` | 4 | 17,573 |
| 合计 | | 60 | 72,959 |

提取准备阶段会逐文件比对冻结清单中的文章与关键词 SHA-256；不读取 Gold 时间线。
后续以 `preparation_report.json` 的实际文章计数为准。原始快照、历史实验与索引未覆盖。

## 代码复用及改动

- 固定上游 https://github.com/nusnlp/LLM-TLS 提交 `13951f669587662434b5f8e8e9c369103ba55a69`。
- 原始文件、GPL-3.0 许可证、各文件下载来源与 SHA-256 保存在 `D:/paper/llm_tls_upstream`。
- 复用官方 one-shot 提示词及 `Sentence.get_time/get_time_format` 原函数；依赖适配避免导入完整上游模型栈。
- 使用现有 `deepseek-v4-flash` API，temperature=0，关闭 thinking，最大输出 512 tokens。
- 沿用官方每篇主要事件摘要和前 1500 article tokens，使用当前模型 tokenizer。示例提示词另外计入输入。
- 这不是全文所有事件的穷尽抽取，也不是原论文 Llama2 数值复现。
- 允许年月/年/UNKNOWN，保留计划和否定等表达，不强制用发布日期填充事件日期。
- 保留文章级发布日期 `date`；生成日期单独位于 `events[].event_date`。
- 同一请求只需一次付费结果，但每篇原文保留映射，不因标题相同直接丢掉文章。
- 官方 `sentence_with_time` 每句只取第一个时间值；另存全部 token 时间提及，避免原始标注信息丢失。

主要文件：

- `chronos_repro/scripts/extract_llm_tls_events.py`
- `chronos_repro/scripts/pilot_llm_tls_extraction.py`
- `chronos_repro/scripts/run_llm_tls_extraction_suite.py`
- `chronos_repro/scripts/LLM_TLS_EXTRACTION.md`
- `chronos_repro/configs/llm_tls_extraction_v1.json`

## 验证与实际调用

离线测试：`tests/test_llm_tls_extraction.py`，6 项通过。覆盖首个时间值、完整时间标注保留、
部分日期与 UNKNOWN、配置变化拒绝混用、重复请求保留原文映射、付费响应缓存恢复不重复调用。

真实 API 试跑：三个数据集各 3 篇，共 9 次返回，8 个不同请求；均可解析。
调用使用 15,275 输入 tokens、205 输出 tokens，共 15,480 tokens。
试跑曾将两个相同输入的来源文章并发提交，额外 1 次调用共 1,415 tokens；已修复试跑提交前去重。
所有实际调用保留在 `pilot_report.json`，额外消耗单独计入 `suite_status.json`，不以缓存去重抹去。
全量任务由 SQLite 的唯一请求键调度，没有该并发重复提交路径。
格式可解析不表示已核验事实正确；没有计算事实一致率或 Gold 指标。

## 当前运行

后台 supervisor PID：31496；准备阶段子进程 PID：36308（仅记录启动时 PID，查询时须核实）。
首次前台准备进程已停止，已提交的主题检查点被后台任务接续；未丢失原始语料。
09:10 UTC 后检查：14 个主题、10,069 篇文章已完成准备，10,018 个不同请求。
该数字是中间快照，不是全量摘要完成数。

调度顺序：准备全部输入 → 使用已确认成功的试跑缓存 → 全量 API（32 并发）→ 导出每主题结果。
后续模型调用已获得本轮授权，不需要再次确认。

输出目录：`chronos_repro/artifacts/llm_tls_extraction_v1/`

- `suite_status.json`：后台阶段、子进程、完成标志及试跑额外费用记账。
- `prepare.log`：逐主题准备进度。
- `progress.json`、`run.log`：全量 API 阶段进度及 usage。
- `extraction.sqlite3`：文章输入、来源、请求和结果；准备与推理均可续跑。
- `responses/`：已返回的付费响应。
- `events/<dataset>/<topic>_events.jsonl`：每主题、每文章映射的摘要与日期；全量结束或显式 export 时生成。
- `export_report.json`：全量调用是否结束及输出是否全部格式有效，分开记录。

## 停止与续跑

API 阶段创建输出目录下的 `STOP` 文件会停止新请求并保存已在途响应；准备阶段结束后也会检查 STOP。
若用户要求立即停止准备阶段，先核实 `suite_status.json` 中的本次进程再结束它们。
异常退出的锁文件只能在核实对应进程不存在后移除。不要更改运行中的提示词、模型或主提取脚本。

在 `D:/paper/chronos_repro` 下：

```powershell
D:/miniforge/envs/tls/python.exe scripts/extract_llm_tls_events.py status
D:/miniforge/envs/tls/python.exe scripts/extract_llm_tls_events.py export
```

结果定位应使用 dataset/topic/source_id 与快照来源，而非把模型摘要当成原文证据。

## 后续更新：断网暂停

用户要求摘要 API 阶段网络不通时暂停。现已接入共享网络等待机制：

- 连接失败、超时或响应中断时，全部工作线程暂停发送新的生成请求；在途返回照常保存。
- 每 30 秒以不携带 API key 的 HEAD 请求检查服务地址连通性，不使用模型生成来探测网络。
- 网络恢复后自动继续未完成的请求；等待中的任务不因断网直接记为永久失败。
- 网络等待期间 STOP 可退出，等待任务恢复为 pending；余额不足和鉴权错误仍停止执行。
- `network_status.json` 记录 online / paused_network 等状态；`transport_policy.json` 记录执行入口哈希。
- 丢失响应的请求可能已在服务端执行，因此不据此承诺网络失败时绝无重复计费。

入口：`chronos_repro/scripts/llm_tls_network_pause.py`。生产 supervisor 已改用该入口。
原提取脚本和原 API 客户端文件 SHA-256 均保持不变，提示词、请求 ID 和已付费缓存无需重建。
原 supervisor 31496 经核实后终止，仅接管调度；预处理 PID 36308 未停止。
新 supervisor PID 35304 使用 `--adopt-prepare-pid 36308` 接续等待，之后自动启动带网络暂停的提取。

测试：网络暂停与提取测试共 11 项通过。模拟并发暂停、恢复、STOP、HTTP/JSON 错误分类、余额终止；
测试没有调用真实模型 API，也未切断机器网络。
