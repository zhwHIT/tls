# Exploration-aware CHRONOS 实验

> 日期：2026-09-04  
> 模型：`deepseek-v4-flash`  
> 数据：Crisis/Egypt，快照 `ece08f344cc94933`

## 一、动机

上一轮自由 CHRONOS 检索到 57 篇文档，却只形成 17 个不同发布日期，±2 天金日期覆盖仅 6.56%。三轮查询连续聚焦 2011 革命和 2013 政变，出现明显的日期坍缩。

因此新增 exploration-aware 策略：不向模型泄漏金时间线，只使用闭域语料的起止时间、当前已检索日期和最大未覆盖区间约束下一轮查询。

## 二、新增实现

- `src/chronos_repro/exploration.py`
  - 读取主题语料时间边界；
  - 根据已覆盖日期计算最大时间缺口；
  - 计算新旧查询的 token Jaccard 相似度；
  - 查询相似度超过 0.75 时使用确定性时间区间查询回退。
- `scripts/run_exploration_baseline.py`
  - 第一轮使用 Direct 查询；
  - 后两轮由 DeepSeek 针对最大未覆盖区间生成查询；
  - 连续两轮没有新增日期则停止；
  - 保存状态、缺口、动作、新文档、新日期和 token 用量。
- `scripts/compare_exploration_dates.py`
  - 对 Direct、Rewrite、自由 CHRONOS 和 exploration-aware CHRONOS 做同口径检索日期覆盖比较。
- `tests/test_exploration.py`
  - 验证最大日期缺口排序和查询相似度。

当前离线测试：`16 passed`。

## 三、真实查询

语料时间范围为 2011-01-16 至 2013-07-22。

| 轮次 | 查询 | 新增日期 |
|---|---|---:|
| 1 | `Egypt crisis timeline` | 16 |
| 2 | `Egypt parliamentary elections 2011-2012 timeline coverage` | 10 |
| 3 | `Egypt Maspero attacks timeline 2011` | 6 |

DeepSeek 调用 2 次，共使用 2,583 tokens：Prompt 1,148，Completion 1,435。未出现余额不足，停止原因是达到最大三轮。

## 四、同口径结果

Egypt 的四条参考时间线合计包含 122 个唯一金日期。以下指标只衡量检索文章发布日期覆盖，不是最终 TLS Date-F1。

| 策略 | 文档 | 不同日期 | 精确覆盖 | 精确召回 | ±2 天覆盖 | ±2 天召回 |
|---|---:|---:|---:|---:|---:|---:|
| Direct | 20 | 16 | 10 | 8.20% | 24 | 19.67% |
| Rewrite | 20 | 16 | 10 | 8.20% | 24 | 19.67% |
| 自由 CHRONOS | 57 | 17 | 8 | 6.56% | 8 | 6.56% |
| Exploration-aware | 41 | 32 | 15 | 12.30% | 31 | 25.41% |

相对 Direct，exploration-aware 的精确日期召回提高约 4.10 个百分点，±2 天召回提高约 5.74 个百分点；同时用 41 篇文档覆盖 32 个日期。相对自由 CHRONOS，文档更少但覆盖显著更广。

## 五、结论与限制

结果支持“显式时间缺口驱动搜索”这一论文主线：更多文档不等于更高时间覆盖，Agent 必须在状态中表示已覆盖日期和未覆盖区间。

但当前只测试了 Egypt 一个主题，不能据此声称整体优于 CHRONOS；语料发布时间也不一定等于文章描述事件的发生日期。因此下一步应：

1. 在 Crisis 四个主题上重复实验；
2. 同时提取句子内事件日期，而不仅使用文章发布日期；
3. 把新增金日期覆盖和查询多样性用于构造初步偏好对；
4. 完成最终时间线生成后再计算正式 Date-F1 和 Timeline-ROUGE。

## 六、产物

- `artifacts/egypt_exploration_chronos_v1.json`
- `artifacts/egypt_exploration_comparison_v1.json`
- `src/chronos_repro/exploration.py`
- `scripts/run_exploration_baseline.py`
- `scripts/compare_exploration_dates.py`
