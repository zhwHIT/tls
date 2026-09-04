# Yemen Exploration 重试与错误分析

## 1. 本次操作

在用户明确要求重新测试后，使用与首次实验完全一致的条件，对 Yemen exploration 进行了一次单次重试：

- Conda 环境：`tls`；
- 模型：`deepseek-v4-flash`；
- API 地址：`https://api.deepseek.com`；
- 冻结语料索引：`crisis_ece08f344cc94933.sqlite3`；
- 检索范围：`crisis yemen`；
- 初始查询：`Yemen crisis timeline`；
- 每轮返回：20 篇；
- 最大轮数：3；
- 密钥继续从 `.env` 读取，没有写入产物或 Git。

为了保留证据，首次失败产物没有删除，而是保存为：

`chronos_repro/artifacts/yemen_exploration_chronos_v1_failed_ssl.json`

成功重试结果成为当前规范产物：

`chronos_repro/artifacts/yemen_exploration_chronos_v1.json`

## 2. 首次错误发生在哪里

首次运行已经完成第 1 轮本地 BM25 检索：

- 已执行查询：`Yemen crisis timeline`；
- 得到 20 篇唯一文档；
- 覆盖 19 个发布日期；
- 在准备第 2 轮探索查询时调用 DeepSeek；
- HTTPS/TLS 连接在读取响应时被提前关闭。

结构化错误为：

```text
DeepSeek connection failed: [SSL: UNEXPECTED_EOF_WHILE_READING]
EOF occurred in violation of protocol (_ssl.c:1007)
```

原运行状态为 `stopped_llm_error`，LLM 用量为 0 tokens。

## 3. 原因分析

### 3.1 可以排除的原因

本次证据可以排除以下常见问题：

- **不是余额不足**：没有收到 HTTP 402 或余额不足响应；错误发生在获得 HTTP 响应之前。
- **不是 API 密钥失效**：同一 `.env` 和同一密钥随后成功完成请求。
- **不是模型名或接口路径错误**：同一模型与 `/chat/completions` 调用在重试时成功。
- **不是固定的证书校验失败**：证书问题通常表现为 `CERTIFICATE_VERIFY_FAILED`；本次是对端在 TLS 流中提前发送 EOF。
- **不是 Yemen 输入必然触发的服务错误**：相同语料、提示和初始检索状态在单次重试后完成。
- **不是本地检索或 JSON 解析失败**：第 1 轮检索已正常写入原失败产物。

### 3.2 最可能原因

最符合现有证据的是一次**瞬时网络链路或服务端连接中断**，可能发生在：

- DeepSeek API 网关或负载均衡器提前关闭连接；
- 本机到服务端之间的代理、NAT 或网络设备重置 TLS 会话；
- 短时网络抖动导致响应流未完整传输。

由于首次失败没有获得 HTTP 响应头、请求 ID 或服务端错误体，仅凭客户端 SSL EOF 无法进一步区分上述三种情况。因此结论是“瞬时传输层故障”的高概率判断，而不是对某个具体网络节点的确定归因。

### 3.3 为什么重试成功很关键

单次重试没有修改代码、模型、密钥、索引、主题或轮数，却成功执行全部流程。这说明故障不具有稳定可复现性，进一步支持瞬时链路问题的判断。

## 4. 重试结果

状态：`ok`
停止原因：`max_rounds`
执行轮数：3
唯一文档数：58
唯一发布日期数：48

实际查询轨迹：

1. `Yemen crisis timeline`
2. `Yemen National Dialogue Conference 2013`
3. `Yemen Hadi sworn in president February 2012`

LLM 用量：

| 类型 | tokens |
|---|---:|
| Prompt | 1,160 |
| Completion | 605 |
| Total | 1,765 |

包含此前所有实验后，累计 DeepSeek 用量由 35,027 增至 **36,792 tokens**。

## 5. Yemen 策略对比

以下仍是“检索文章发布日期覆盖参考时间线日期”的诊断结果，不是最终 TLS Date-F1。

| 策略 | 文档数 | 唯一日期数 | 精确日期召回 | ±2 天召回 |
|---|---:|---:|---:|---:|
| direct | 20 | 19 | 8.64% | 29.63% |
| rewrite | 20 | 19 | 8.64% | 29.63% |
| free_chronos | 39 | 24 | 3.70% | 22.22% |
| exploration_chronos | 58 | 48 | **14.81%** | **37.04%** |

在 Yemen 上，exploration 相比 direct：

- 唯一日期增加 29 个；
- 精确日期召回提高 6.17 个百分点；
- ±2 天召回提高 7.41 个百分点。

## 6. 更新后的四主题严格配对结果

Yemen 成功后，Egypt、Libya、Syria、Yemen 四个主题现在都可以进行严格配对宏平均：

| 策略 | 平均文档数 | 平均唯一日期数 | 宏平均精确召回 | 宏平均 ±2 天召回 |
|---|---:|---:|---:|---:|
| direct | 20.00 | 16.75 | 5.95% | 19.58% |
| rewrite | 20.00 | 16.75 | 6.19% | 19.82% |
| free_chronos | 52.75 | 33.75 | 6.76% | 22.01% |
| exploration_chronos | 52.25 | 42.50 | **9.86%** | **26.45%** |

补齐 Yemen 后，exploration 在四主题宏平均中仍然取得最高日期多样性和最高日期覆盖召回，为后续构造基于探索收益的 SFT/DPO 数据提供了更完整证据。

## 7. 产物更新

- `yemen_exploration_chronos_v1_failed_ssl.json`：保留首次 SSL EOF 失败现场；
- `yemen_exploration_chronos_v1.json`：单次重试成功的完整三轮轨迹；
- `crisis_query_strategy_aggregate_v1.json`：已重算为四主题严格配对汇总，`failures` 为空。

## 8. 验证

执行：

```powershell
conda run -n tls python -m pytest -q chronos_repro\tests
```

结果：`16 passed in 12.06s`。

## 9. 后续建议

目前无需为余额错误增加重试，余额不足仍必须立即停止。若后续希望提升长时间批量实验的稳定性，可单独为 `URLError`、连接重置和 SSL EOF 增加有上限的指数退避，并满足：

- 最多重试 1～2 次；
- HTTP 402/余额不足永不重试；
- 每次失败写入错误类型和尝试次数；
- 成功结果保留请求 ID；
- 不对输入错误、认证错误和其他确定性 HTTP 4xx 重试。

本次没有擅自修改客户端重试策略，只按用户指令执行了一次人工重试。
