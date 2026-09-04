# P1 Crisis BM25 检索实施记录

> 日期：2026-09-04  
> 数据快照：`ece08f344cc94933`  
> 状态：索引构建、检索接口与测试完成

## 一、T17 与 Entities 下载暂停

官方 `news-tls` 将 T17、Crisis、Entities 放在同一个 Google Drive 总目录：

<https://drive.google.com/drive/folders/1gDAF5QZyCWnF_hYKbxIzOyjT6MSkbQXu?usp=sharing>

进入其中的 `t17/` 或 `entities/` 子目录即可。按照当前决定，下载暂停；`closed_corpora_download/t17` 与 `closed_corpora_download/entities` 保留现场，但不冻结、不建正式索引、不训练、不报告分数。

## 二、实现方案

采用 Python 当前运行时自带的 SQLite FTS5/BM25，不新增 pip 依赖。索引包含标题、全文、发布时间、doc ID 和主题；标题权重为 2、正文权重为 1，并强制按 Crisis 主题过滤。

`src/chronos_repro/retrieval.py` 提供索引构建、单查询检索和兼容 CHRONOS `search_engine="crisis <topic>"` 的多查询适配器。多查询按各查询名次交错合并并以 doc ID 去重。输出保留 `id/title/snippet/url/timestamp`，另加 `score/topic` 供轨迹分析。

`tests/test_retrieval.py` 验证微型压缩语料的索引、元数据、主题检索和结果字段。当前测试为 `9 passed`。

## 三、真实索引结果

索引：`D:\paper\chronos_repro\artifacts\crisis_ece08f344cc94933.sqlite3`

| 项目 | 结果 |
|---|---:|
| 文档总数 | 17,573 |
| egypt | 4,083 |
| libya | 4,274 |
| syria | 5,170 |
| yemen | 4,046 |
| 索引大小 | 142,721,024 bytes |

索引元数据绑定快照 ID 与 manifest SHA-256。构建报告为 `artifacts/crisis_bm25_build.json`。

## 四、检索烟测

用 `Hosni Mubarak resignation protests` 和 `Egypt presidential election` 两个查询，限定 `crisis egypt`，成功返回并去重 20 篇新闻。前列结果包括 2011-02-11 穆巴拉克辞职报道和 2012-06-24 埃及总统选举报道。结果为 `artifacts/crisis_egypt_bm25_smoke.json`。

多查询输出按查询名次交错，不把不同查询的 BM25 原始分数直接混排；BM25 分数只在同一查询内有可靠的相对意义。

## 五、运行命令

```powershell
conda run -n tls chronos-repro build-index `
  --data snapshots\crisis\ece08f344cc94933 `
  --dataset crisis `
  --output artifacts\crisis_ece08f344cc94933.sqlite3 `
  --report artifacts\crisis_bm25_build.json

conda run -n tls chronos-repro search `
  --index artifacts\crisis_ece08f344cc94933.sqlite3 `
  --search-engine "crisis egypt" `
  --query "Hosni Mubarak resignation protests" `
  --query "Egypt presidential election" `
  --top-k 20 `
  --output artifacts\crisis_egypt_bm25_smoke.json
```

## 六、下一步

固定 Direct、Rewrite、CHRONOS 三条基线共同使用的 top-k、最大轮数和停止条件；然后实现 JSONL 轨迹缓存，逐轮记录 `state → action/query → observation(doc IDs/scores) → updated_state`。
