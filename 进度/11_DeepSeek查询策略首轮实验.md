# DeepSeek 查询策略首轮实验

> 日期：2026-09-04  
> 模型：`deepseek-v4-flash`  
> API：`https://api.deepseek.com`  
> 数据：Crisis 快照 `ece08f344cc94933`，主题 `egypt`

## 一、安全配置

真实密钥保存于本地 `chronos_repro/.env`。仓库通过本地 `core.excludesFile` 指向 `.git-secret-ignore`，`git check-ignore` 已确认 `.env` 被排除。代码、配置、日志和实验 JSON 均不保存密钥。

新增：

- `src/chronos_repro/envfile.py`：读取简单 `.env`，只返回加载的变量名，不输出值；
- `src/chronos_repro/llm.py`：DeepSeek OpenAI-compatible 客户端；
- `src/chronos_repro/strategies.py`：Direct、Rewrite、CHRONOS 查询函数；
- `configs/llm_deepseek_v1.json`：不含密钥的模型配置；
- `scripts/deepseek_smoke.py`：最小连通性测试；
- `scripts/run_query_baselines.py`：三策略真实查询实验；
- `tests/test_envfile.py`、`tests/test_llm.py`：密钥缺失、余额不足和策略测试。

余额不足识别为终止错误：HTTP 402 或响应包含 `insufficient balance`、`余额不足` 等标记时立即写入停止状态，不进行自动重试。

## 二、此前错误与修复

此前唯一失败测试为 `asdict() should be called on dataclass instances`。原因是测试替身返回普通 `Result` 类，而真实客户端返回 `ChatResult` dataclass。测试替身改为真实 `ChatResult` 后问题消失。当前测试结果：

```text
14 passed
```

该错误来自测试模拟对象不一致，不是 DeepSeek API、模型余额或生产客户端错误。

## 三、API 烟测

最小请求要求模型仅返回 `TLS_OK`，结果成功：

| 项目 | 结果 |
|---|---|
| 状态 | `ok` |
| 返回模型 | `deepseek-v4-flash` |
| 内容 | `TLS_OK` |
| Prompt tokens | 89 |
| Completion tokens | 18 |
| Total tokens | 107 |

结果：`chronos_repro/artifacts/deepseek_v4_flash_smoke.json`。

## 四、三种查询策略

固定参数：BM25 top-k 20、temperature 0、CHRONOS 最多 3 轮。

### Direct

不调用 LLM，直接检索：

```text
Egypt crisis timeline
```

返回 20 篇文档。

### Rewrite

调用 LLM 一次，将主题改写为：

```text
Timeline of Egypt crisis
```

返回 20 篇文档。

### CHRONOS

进行三轮证据条件自提问：

1. `Egypt crisis 2011 revolution to 2013 coup: key events and dates`
2. `Egypt June 30 2013 anti-Morsi protests and Tamarod role in triggering the military ultimatum: key details and dates`
3. `Egypt July 3 2013 military ouster of Morsi: exact date of army statement, suspension of constitution, and appointment of Adly Mansour`

三轮去重得到 57 篇文档。Rewrite 与 CHRONOS 共调用模型 4 次，总用量：

| 项目 | Tokens |
|---|---:|
| Prompt | 3,833 |
| Completion | 1,296 |
| Total | 5,129 |

未出现余额不足。结果：`artifacts/egypt_query_baselines_deepseek_v1.json`。

## 五、金日期覆盖诊断

将检索文档发布日期与 Egypt 全部参考时间线日期比较。这里是**检索诊断**，不是最终生成时间线的 Date-F1。

| 策略 | 文档 | 唯一文档日期 | 精确日期召回 | ±2 天日期召回 | ±2 天检索日期精度 |
|---|---:|---:|---:|---:|---:|
| Direct | 20 | 16 | 8.20% | 19.67% | 87.50% |
| Rewrite | 20 | 16 | 8.20% | 19.67% | 87.50% |
| CHRONOS | 57 | 17 | 6.56% | 6.56% | 70.59% |

诊断结果：`artifacts/egypt_query_baselines_date_diagnostic.json`。

## 六、结果解释

当前 CHRONOS 检索的文档更多，但日期覆盖更差。三轮查询逐渐集中到 2011 革命、2013 反 Morsi 抗议和军事罢免，属于“围绕已知高显著事件继续深挖”，没有探索参考时间线的其他阶段。

这验证了项目主线的必要性：下一步不能只让 LLM 查看已有摘要后自由提问，而应显式维护：

- 已覆盖日期及时间区间；
- 长时间空白区间；
- 已覆盖事件簇；
- 未解决的因果桥和事件要素；
- 每轮新增日期和新增事件的边际收益。

查询生成时需要要求模型优先覆盖未探索时间段，并对与历史查询高度相似的候选查询降权或拒绝。停止条件也不能只看文档是否新增，而应同时看新日期、新事件和证据覆盖是否增加。

## 七、下一步

1. 为轨迹状态加入 `covered_dates`、`date_gaps` 和 query 相似度；
2. 实现 exploration-aware CHRONOS，与当前自由自提问版本对照；
3. 在 Crisis 四个主题而非单一 Egypt 上运行检索日期诊断；
4. 把“覆盖更多金日期”的查询作为 chosen，把日期坍缩查询作为 rejected，形成初步 DPO 偏好对；
5. 查询策略稳定后再进入最终时间线生成，避免在低覆盖证据上评测生成模型。
