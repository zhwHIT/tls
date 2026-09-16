# 多语言 BERT 与 BM25 混合检索重构

日期：2026-09-08

## 1. 本次目标

原闭域检索仅使用 SQLite FTS5/BM25。它对英文实体词和精确短语有效，但面对同义改写、概念性查询以及“中文 query 检索英文文章”时召回明显受限。只要相关证据没有进入候选集，后续 VERIFY、MERGE 和时间线模型都无法补救。

因此本次把冻结闭域检索器升级为双通道：

- lexical：保留 BM25，负责实体、日期和精确词面匹配；
- semantic：使用多语言 BERT 句向量，负责跨语言和同义语义匹配；
- fusion：用 weighted reciprocal rank fusion（加权 RRF）融合排名；
- post-process：按内容哈希去重，再做很弱的同日重复惩罚；
- evidence：保留两通道原始排名和原始分数，供 VERIFY、轨迹回放和消融分析。

Gold 时间线不进入运行时检索，也不参与 embedding 或排序，只能在 train/dev 离线构造教师偏好或评测召回，避免数据泄漏。

## 2. 环境与安装

所有 Python、pip、建索引和测试均在 Conda 环境 tls 内执行：

~~~powershell
conda activate tls
python -m pip install sentence-transformers==5.1.2 --progress-bar off -i https://pypi.tuna.tsinghua.edu.cn/simple
~~~

安装结果：

- Python：3.10.14；
- sentence-transformers：5.1.2；
- 安装位置：D:\miniforge\envs\tls\Lib\site-packages；
- 当前 PyTorch 未检测到 CUDA，因此真实向量索引使用 CPU 构建；
- 此过程未调用 DeepSeek API。

## 3. 模型选择与冻结

模型采用 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2：

- 支持 50 种语言；
- BERT 架构，输出 384 维向量；
- 本地最大序列长度为 128 token；
- 相比 LaBSE 更小，更适合毕业设计在 CPU/普通显卡上复现；
- 固定 revision：e8f8c211226b894fcb81acc59f3b34ba3efd5f42。

本地目录：

~~~text
D:\paper\chronos_repro\models\paraphrase-multilingual-MiniLM-L12-v2
~~~

已排除重复格式的 PyTorch bin、TensorFlow、ONNX 和 OpenVINO 权重，只保留 safetensors 与 tokenizer。模型正文文件共 14 个，约 476.42 MiB。大型权重已加入 .gitignore，不推送 Git；可复现的模型 ID、revision 和参数保存在 configs/hybrid_retrieval_v2.json。

## 4. 重新设计后的完整检索流程

~~~text
Controller 当前 timeline + memory
        |
        v
生成一个或多个 query
        |
        +------------------------+
        |                        |
        v                        v
SQLite FTS5/BM25          多语言 BERT passage embedding
实体/日期/精确词面         同义、概念、中文到英文
        |                        |
        +-----------+------------+
                    v
       每个通道扩大候选池 top_k × 5
                    |
                    v
       weighted RRF（默认 1:1，k=60）
                    |
                    v
       内容哈希去重 + 轻量日期多样化
                    |
                    v
   多篇 evidence -> VERIFY -> APPEND/UPDATE/DROP
                    |
                    v
       更新时间线与 memory，再 SEARCH 或 STOP
~~~

RRF 的动机是 BM25 分数和余弦相似度量纲不同，不能直接线性相加。RRF 只使用通道内排名，既稳定又容易消融。每篇文章按“标题 + 正文 passage”编码；passage 默认 600 字符、重叠 100 字符，以适配模型 128-token 上限。文档得分取其最佳 passage，既能定位局部证据，又避免整篇长文被截断。

多结果处理没有交给 Retriever 直接下结论。Retriever 返回候选及证据片段，VERIFY 对每篇候选分别判断支持、冲突或无关，MERGE 再逐项选择 APPEND、UPDATE 或 DROP；需要更多原文时按 document ID 读取受限长度正文。

## 5. 新增或修改文件

| 文件 | 作用 |
|---|---|
| src/chronos_repro/dense_retrieval.py | 文档分块、SentenceTransformer 适配、流式向量索引、topic 内余弦检索、文档级 max-passage 聚合 |
| src/chronos_repro/retrieval.py | 识别 hybrid manifest、加权 RRF、精确去重、日期多样化，并保持旧 search 接口兼容 |
| src/chronos_repro/cli.py | 新增 build-dense-index 与 create-hybrid-index 命令 |
| configs/hybrid_retrieval_v2.json | 固定模型 revision、分块、向量类型、RRF 权重和候选池参数 |
| scripts/compare_retrievers.py | 在相同 query/top-k 下比较 BM25 与 hybrid，并输出发布时间日期覆盖诊断 |
| tests/test_dense_retrieval.py | 用离线 stub encoder 测试跨语言检索、分块、RRF 和兼容性 |
| pyproject.toml | 增加 retrieval 可选依赖 |
| environment.yml | 在 tls 环境声明 sentence-transformers 版本 |
| README.md | 增加完整建索引与查询命令 |
| 时间线_Search_Agent_框架研究方案.md | 新增 v2 混合检索、Gold 边界与消融设计 |

## 6. 已完成验证

### 6.1 真模型跨语言验证

使用本地模型编码三句话：

- 中文：政府宣布停火协议；
- 英文同义：The government announced a ceasefire agreement；
- 无关英文：A football team won the championship。

结果：

| 对比 | 余弦相似度 |
|---|---:|
| 中文—英文同义 | 0.8597 |
| 中文—无关足球 | 0.0904 |

说明本地权重可正常加载，且确实提供 BM25 不具备的中文到英文语义召回能力。

### 6.2 自动测试

在 tls 环境运行完整测试：

~~~powershell
$env:PYTHONPATH = "src"
python -m pytest -q
~~~

最终结果为 59 passed。第一次测试曾有 2 个失败，原因是 Windows 不允许在 numpy memmap 仍持有文件句柄时重命名目录；已在原子落盘前显式关闭映射，修复后全部通过。另增加了 JSON null 标题回归测试，避免将空标题错误索引为字符串 None。

### 6.3 T17 真实索引规模

T17 BM25 索引包含 4,203 篇文章。按新配置预扫描得到 51,571 个 passage、正文约 21,437,491 字符，预计 float16 向量矩阵约 39.6 MB。CPU 全量试建 20 分钟时完成 10,944/51,571（21.22%），预计还需约一小时，因此安全停止并删除仅含生成向量的临时目录，源语料和正式索引未受影响。

随后增加 build-dense-index --topic 参数和范围校验，完成 T17/mj pilot。清洗 null 标题后，正式 v2 dense 索引包含 723 个有效 passage；7 篇标题和正文均为空的文章不进入语义索引。向量为 384 维 float16，723/723 行均为有限非零值。hybrid manifest 仅暴露 mj，不能被误用于其他 T17 主题。

### 6.4 真实中英文检索对照

使用相同冻结语料、相同 top-20 和同一组 38 个 Gold 日期做 publication-date 代理诊断：

| Query | Retriever | 返回文档 | 精确日期覆盖 | ±2 天日期覆盖 |
|---|---|---:|---:|---:|
| 迈克尔杰克逊 去世 葬礼 纪念 | BM25 | 0 | 0/38（0%） | 0/38（0%） |
| 迈克尔杰克逊 去世 葬礼 纪念 | hybrid 1:1 | 20 | 3/38（7.89%） | 10/38（26.32%） |
| Michael Jackson death funeral memorial | BM25 | 20 | 3/38（7.89%） | 13/38（34.21%） |
| Michael Jackson death funeral memorial | hybrid 1:1 | 20 | 2/38（5.26%） | 8/38（21.05%） |
| 同一英文 query | hybrid 2:1 | 20 | 3/38（7.89%） | 9/38（23.68%） |

中文结果证明 dense 解决了 BM25 完全不可达的问题；英文结果则证明简单 1:1 或 2:1 并不保证排序全面优于强 BM25。不能在单个 mj 主题上继续追权重，下一步应在 train/dev 多主题建立 event–passage 相关性标签，再选择固定权重或自适应门控。

Windows 上通过 conda run 传中文参数时终端显示曾出现乱码。重跑改用 tls 环境解释器 D:\miniforge\envs\tls\python.exe 并设置 PYTHONUTF8=1；产物中 query 码点已校验为正确中文。该问题只发生在命令行参数/终端编码层，不是模型编码问题。

## 7. 正式实验要求

至少保留以下消融：

1. BM25；
2. dense；
3. BM25 + dense weighted RRF；
4. hybrid + 内容去重 + 日期多样化；
5. 在相同 hybrid 环境下比较无 Memory、coverage memory、coverage + gap memory；
6. 在相同 hybrid 环境下比较规则 SEARCH/STOP、SFT、SFT + DPO。

检索层报告 Evidence Recall@k、Gold-date Recall@k、MRR/nDCG、唯一日期数和重复率；系统层报告 Date-F1、Timeline-ROUGE、证据支持率、查询数、检索文档数与墙钟时间。

注意：文章 publication date 不一定等于正文事件发生日期，因此 compare_retrievers.py 的日期覆盖只能作为快速诊断，不能冒充最终 Evidence Recall。正式 Evidence Recall 需要 Gold event 与 passage 的人工或 LLM 相关性标签，并在 dev/test 保持隔离。

## 8. 下一步

1. 在 train/dev 构造 event–passage 相关性标签，而不是只依赖 publication date；
2. 比较固定 RRF、词面充分性门控和 BM25 保底配额三种融合策略；
3. 在 train/dev 上调节 BM25:dense 权重、候选池倍数与日期多样化强度；
4. 固定检索器后重新执行第一阶段 rollout 和第二阶段 Gold 教师标注；
5. 优先标注 BM25 漏检但 dense 命中的证据，形成 query 正负例及 SEARCH/STOP 偏好；
6. 按 topic 分批构建 T17/Crisis/Entities，或在有 CUDA 的机器执行全量构建，避免当前 CPU 上不可控的长任务。
