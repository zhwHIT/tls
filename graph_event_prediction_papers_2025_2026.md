# 2025-2026 顶会「图谱 × 事件预测/推理」论文综述

> **整理日期**：2026-07-13
> **主题**：利用知识图谱 / 时间图谱 / 事件图谱进行事件预测（Event Prediction / Forecasting）或事件推理（Event Reasoning）
> **涵盖会议**：ACL, EMNLP, AAAI, NeurIPS, ICLR, WWW, KDD 等
> **说明**：链接均来自网络检索，建议点击核对最新状态（部分为 Findings / Workshop / 预印本）

---

## 目录

1. [时间知识图谱（TKG）预测与推理](#一时间知识图谱tkg预测与推理)
2. [事件因果图 / 事件图谱推理](#二事件因果图--事件图谱推理)
3. [时空图谱事件预测（Spatio-Temporal）](#三时空图谱事件预测spatio-temporal)
4. [LLM + 图谱融合](#四llm--图谱融合)
5. [汇总总表](#五汇总总表)

---

## 一、时间知识图谱（TKG）预测与推理

### 1. CFEP: Conformal Event Prediction with Temporal Knowledge Graph — ACL 2026 Findings 🔥

| 项 | 内容 |
|---|---|
| **作者** | Cheng Hu, Cong Cao, Fangfang Yuan, Diandian Guo, Pin Xu, Yu Liu, Yanbing Liu（中科院信工所等） |
| **会议** | Findings of ACL 2026 (pp. 5233–5248) |
| **链接** | [ACL Anthology](https://aclanthology.org/2026.findings-acl.258/) · [GitHub](https://github.com/hucheng-IIE/CFEP) |

**创新点：**
- 面向**高风险领域**（军事、公共安全、医疗）的时间知识图谱事件预测
- 指出现有 TKG 方法**缺乏严格的不确定性量化**，限制了决策可靠性
- 提出 **CFEP** —— 基于**共形预测（Conformal Prediction）**的框架，提供统计覆盖保证
- 两大组件：**非共形分数扩散**（捕捉拓扑 + 时间不确定性）+ **效率感知优化算法**（缩小覆盖差距）
- 在三个公开数据集上一致保证统计覆盖率同时提升效率

---

### 2. AnRe: Analogical Replay for Temporal Knowledge Graph Forecasting — ACL 2025 Long

| 项 | 内容 |
|---|---|
| **会议** | ACL 2025 Long Paper (2025.acl-long.231) |
| **链接** | [ACL Anthology](https://aclanthology.org/2025.acl-long.231/) |

**创新点：**
- 针对**时间知识图谱预测（TKG Forecasting）**任务
- **类比重放（Analogical Replay）**机制：通过��比历史相似事件进行未来事件推理
- 结合历史模式与类比推理增强外推能力

---

### 3. A Multi-Expert Structural-Semantic Hybrid Framework for Unveiling Historical Patterns in TKG — ACL 2025 Findings

| 项 | 内容 |
|---|---|
| **会议** | Findings of ACL 2025 (2025.findings-acl.1056) |
| **链接** | [ACL Anthology](https://aclanthology.org/2025.findings-acl.1056/) |

**创新点：**
- **多专家（Multi-Expert）结构-语义混合框架**
- 挖掘时间知识图谱中的**历史模式（Historical Patterns）**
- 结合结构信息与语义信息进行事件预测

---

### 4. Risk-Controlled Event-Driven Cascading Updates for KG Consistency Restoration — ACL 2026 Findings

| 项 | 内容 |
|---|---|
| **作者** | Bo Ni, Qinwen Ge, Haowei Fu, Ryan A. Rossi, Xiaorui Liu, Jiejun Xu, Tyler Derr |
| **会议** | Findings of ACL 2026 (pp. 42534–42548) |
| **链接** | [ACL Anthology](https://aclanthology.org/2026.findings-acl.2111/) |

**创新点：**
- 解决动态知识图谱中**事件驱动的级联更新**问题——单个局部更新可能使既有正确知识失效
- **共形预测**在整个级联链上提供不确定性保证，兼顾多跳候选间的依赖
- **基于图的评分框架 + LLM** 用世界知识丰富事件表示
- 需要**协调式多跳推理**恢复一致性

---

### 5. TEILP: Time Prediction over Knowledge Graphs via Logical Reasoning — AAAI 2024（基线经典）

| 项 | 内容 |
|---|---|
| **作者** | Siheng Xiong, Yuan Yang, Ali Payani, James C. Kerce, Faramarz Fekri（Georgia Tech / Cisco） |
| **会议** | AAAI 2024 (Vol. 38 No. 14) |
| **链接** | [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/29544) |

**创新点：**
- 将 TKG 转换为**时间事件知识图谱（TEKG）**，显式表示时间
- 使用**可微随机游走 + 条件概率密度函数**进行时间预测
- 在 5 个基准上超越基线，提供**可解释的逻辑推理**解释
> *注：2024 年论文，作为 2025-2026 TKG 逻辑推理工作的重要基线收录参考*

---

## 二、事件因果图 / 事件图谱推理

### 6. SeDGPL: Predicting Consequences from An Event Causality Graph（CGEP 任务）

| 项 | 内容 |
|---|---|
| **作者** | Chuanhong Zhan, Wei Xiang, Chao Liang, Bang Wang（华中科技大学 / 华中师范大学） |
| **链接** | [arXiv 2409.17480](https://arxiv.org/abs/2409.17480) · [GitHub](https://github.com/zhanchuanhong/SeDGPL) |

**创新点：**
- 提出 **CGEP（Causality Graph Event Prediction）任务**：从**事件因果图（ECG）**预测后果事件，而非线性事件链
- **SeDGPL** 模型三大模块：
  - **DsGL（距离敏感图线性化）**：按到锚点事件的距离重排因果三元组，转为图提示模板
  - **EeCE（事件增强因果编码）**：融合事件上下文语义与图 schema 信息
  - **ScEP（语义对比事件预测）**：监督对比学习 + [MASK] 提示学习预测后果事件
- 构建 **CGEP-MAVEN / CGEP-ESC** 两个数据集，超越 Llama3-7B、GPT-3.5-turbo 等

---

### 7. Document-Level Future Event Prediction Integrating Event Knowledge Graph and LLM Temporal Reasoning — Electronics 2025

| 项 | 内容 |
|---|---|
| **期刊** | Electronics (MDPI), Vol. 14(19), 3827, 2025 |
| **链接** | [MDPI](https://www.mdpi.com/2079-9292/14/19/3827) |

**创新点：**
- **文档级未来事件预测**
- 整合**事件知识图谱（Event KG）** + **LLM 时间推理**
- 图谱结构化知识与 LLM 推理能力协同

---

## 三、时空图谱事件预测（Spatio-Temporal）

### 8. TEN-DM: Topology-Enhanced Diffusion Model for Spatio-Temporal Event Prediction — ICLR 2026 🔥

| 项 | 内容 |
|---|---|
| **作者** | Yuxin Liu, Kaiming Wang, Chenguang Yang, Yulia Gel, Yuzhou Chen |
| **会议** | ICLR 2026 (Poster, forum id BZ1vutP53o) |
| **链接** | [ICLR](https://iclr.cc/virtual/2026/poster/10010937) · [OpenReview](https://openreview.net/forum?id=BZ1vutP53o) |

**创新点：**
- 面向**时空点过程（STPP）**数据的事件预测
- 指出现有深度方法**独立处理空间和时间**，忽略时空依赖
- 两大核心：**时空图构建** + **多模态拓扑特征表示学习**
- **拓扑增强扩散模型**结合时间查询技术捕捉周期性时间模式

---

### 9. GSTPP: Fine-grained Spatio-temporal Event Prediction with Self-adaptive Anchor Graph

| 项 | 内容 |
|---|---|
| **作者** | Wang-Tao Zhou, Zhao Kang, Sicong Liu, Lizong Zhang, Ling Tian（电子科技大学） |
| **链接** | [arXiv 2501.08653](https://arxiv.org/abs/2501.08653) |

**创新点：**
- **图时空点过程（GSTPP）**模型用于细粒度事件预测
- **自适应锚图（SAAG）**：自适应定位锚节点并学习节点间关联边，无需显式空间边界
- **L-GCN（位置感知 GCN）** + **RLE（相对位置编码器）** + **神经 ODE** 建模状态演化
- 在地震、COVID-19、CitiBike 三数据集超越 NSTPP、DSTPP 等

---

### 10. Expand and Compress: Tuning Principles for Continual Spatio-Temporal Graph Forecasting — ICLR 2025

| 项 | 内容 |
|---|---|
| **会议** | ICLR 2025 |
| **链接** | [ICLR](https://proceedings.iclr.cc/paper_files/paper/2025/hash/cb2266111eadcfa2c02187ace64e2183-Abstract-Conference.html) · [arXiv 2410.12593](https://arxiv.org/abs/2410.12593) · [GitHub](https://github.com/Onedean/EAC) |

**创新点：**
- **持续时空图预测（Continual STG Forecasting）**：应对流式接收的时空数据
- **EAC（Expand and Compress）**调优原则：扩展与压缩机制
- 解决 STGNN 在动态流场景下的灾难性遗忘

---

## 四、LLM + 图谱融合

### 11. ReaL-TG: Self-Exploring Language Models for Explainable Link Forecasting on Temporal Graphs — NeurIPS 2025 Workshop

| 项 | 内容 |
|---|---|
| **作者** | Zifeng Ding, Shenyang Huang, Zeyu Cao, Emma Kondrup, ... Michael Bronstein, Andreas Vlachos |
| **会议** | NeurIPS 2025 Workshop: New Perspectives in Graph Machine Learning |
| **链接** | [NeurIPS](https://neurips.cc/virtual/2025/loc/san-diego/127624) |

**创新点：**
- **可解释的时间图链接预测**——用强化学习微调 LLM
- **ReaL-TG** 框架：基于结果的奖励，让模型自我探索图结构推理策略
- 微调 Qwen3-4B → **ReaL-TG-4B**，在排序指标上超越 GPT-5 mini
- 引入 **LLM-as-a-Judge** 评估推理链质量与幻觉

---

### 12. A Comprehensive Evaluation of LLMs on Temporal Event Forecasting

| 项 | 内容 |
|---|---|
| **作者** | He Chang, Chenchen Ye, Zhulin Tao, ... Yunshan Ma, Tat-Seng Chua（中国传媒大学 / UCLA / NUS 等） |
| **链接** | [arXiv 2407.11638](https://arxiv.org/abs/2407.11638) |

**创新点：**
- 系统评估 LLM 的**时间事件预测**能力
- 构建 **MidEast-TE-mini** 基准：**图结构事件 + 原始新闻文本**结合
- 关键发现：直接把原始文本喂给 LLM **无法提升**零样本外推；**图结构方法更优**
- RAG 检索模块能有效捕捉历史事件中的时间关系模式

---

### 13. TKG-LLM: Temporal Knowledge Graph as Enhanced Prompt Learning with LLM — NeurIPS 2025（OpenReview）

| 项 | 内容 |
|---|---|
| **会议** | NeurIPS 2025（OpenReview 提交） |
| **链接** | [OpenReview](https://openreview.net/forum?id=8OrJvzPdUm) |

**创新点：**
- 将**时间知识图谱作为增强提示学习**辅助 LLM 时间序列预测
- 在多个基准上超越基线

---

## 五、汇总总表

| # | 论文简称 | 会议/来源 | 年份 | 图谱类型 | 任务 | 链接 |
|---|----------|-----------|------|----------|------|------|
| 1 | **CFEP** | ACL Findings | 2026 | 时间知识图谱 | 事件预测 + 不确定性量化 | [链接](https://aclanthology.org/2026.findings-acl.258/) |
| 2 | **AnRe** | ACL Long | 2025 | 时间知识图谱 | TKG 预测（类比重放） | [链接](https://aclanthology.org/2025.acl-long.231/) |
| 3 | **Multi-Expert TKG** | ACL Findings | 2025 | 时间知识图谱 | 历史模式挖掘预测 | [链接](https://aclanthology.org/2025.findings-acl.1056/) |
| 4 | **Risk-Controlled Cascading** | ACL Findings | 2026 | 动态知识图谱 | 事件驱动级联更新推理 | [链接](https://aclanthology.org/2026.findings-acl.2111/) |
| 5 | **TEILP** | AAAI | 2024 | 时间知识图谱 | 时间预测 + 逻辑推理 | [链接](https://ojs.aaai.org/index.php/AAAI/article/view/29544) |
| 6 | **SeDGPL** | arXiv | 2024 | 事件因果图 | 后果事件预测（CGEP） | [链接](https://arxiv.org/abs/2409.17480) |
| 7 | **Doc-Level Event KG+LLM** | Electronics | 2025 | 事件知识图谱 | 文档级未来事件预测 | [链接](https://www.mdpi.com/2079-9292/14/19/3827) |
| 8 | **TEN-DM** | ICLR | 2026 | 时空图 | 时空事件预测（扩散） | [链接](https://iclr.cc/virtual/2026/poster/10010937) |
| 9 | **GSTPP** | arXiv | 2025 | 自适应锚图 | 细粒度时空事件预测 | [链接](https://arxiv.org/abs/2501.08653) |
| 10 | **EAC** | ICLR | 2025 | 时空图 | 持续时空图预测 | [链接](https://arxiv.org/abs/2410.12593) |
| 11 | **ReaL-TG** | NeurIPS Workshop | 2025 | 时间图 | 可解释链接预测（RL+LLM） | [链接](https://neurips.cc/virtual/2025/loc/san-diego/127624) |
| 12 | **LLM Temporal Eval** | arXiv | 2024 | 图结构事件 | LLM 时间事件预测评估 | [链接](https://arxiv.org/abs/2407.11638) |
| 13 | **TKG-LLM** | NeurIPS | 2025 | 时间知识图谱 | 图谱增强 LLM 预测 | [链接](https://openreview.net/forum?id=8OrJvzPdUm) |

---

## 技术趋势观察

| 技术方向 | 代表工作 | 趋势 |
|----------|----------|------|
| **不确定性量化 / 共形预测** | CFEP (ACL 2026), Risk-Controlled Cascading (ACL 2026) | 🔥 2026 新兴热点 |
| **LLM + 图谱推理（可解释）** | ReaL-TG (NeurIPS 2025), TKG-LLM | ⭐⭐⭐⭐ 上升中 |
| **事件因果图预测** | SeDGPL (CGEP), Doc-Level Event KG | ⭐⭐⭐ 拓展中 |
| **时空点过程 + 图 / 扩散** | TEN-DM (ICLR 2026), GSTPP | ⭐⭐⭐⭐ 热门 |
| **时间知识图谱外推** | AnRe, Multi-Expert, TEILP | ⭐⭐⭐⭐⭐ 成熟 |
| **持续 / 流式图学习** | EAC (ICLR 2025) | ⭐⭐⭐ 新兴 |

---

> **注**：
> 1. 本综述聚焦「图谱 + 事件预测/推理」交叉领域，与你现有的 TLS（时间线摘要）综述形成互补。
> 2. 编号 5、6、12 虽为 2024 年成果，但作为 2025-2026 相关工作的核心基线 / 高被引参考予以收录。
> 3. 部分 NeurIPS/ICLR 条目为 Workshop 或 Poster，正式收录状态请以官方为准。
