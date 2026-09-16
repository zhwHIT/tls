# Egypt Phase 2 Controller 完整轨迹

> 任务：`crisis:egypt:gold-3b8c742b542f:missing_date:phase2`  
> 阶段：`GAP_REFINEMENT`  
> 说明：本文展示实际保存的短结构化 thought，不代表或披露模型隐藏推理。该 pilot 是 Controller 单步轨迹，真实执行到 SEARCH observation，后续 VERIFY/MERGE 尚未执行。

## 1. 教师私有目标

学生不可见，训练数据生成器和教师可见：

- 缺口类型：`MISSING_DATE`
- 目标事件：埃及新议会召开首次会议
- canonical date：`2012-01-24`
- accepted dates：`2012-01-23`、`2012-01-24`

Gold 仅用于候选 query 的离线评分，没有写入 Controller 的用户输入。

## 2. 初始可见时间线

| event ID | 日期 | 事件 |
|---|---|---|
| gold-a1405de98dda | 2012-01-16 | 埃及请求 IMF 提供32亿美元以填补政治经济动荡造成的预算缺口 |
| gold-57253c7593e8 | 2012-01-21 | 伊斯兰政党在议会选举中取得约四分之三席位 |
| target_masked | 未知 | 埃及新议会召开首次会议，但日期被遮蔽 |
| gold-b6122d0862b2 | 2012-04-14 | 选举委员会取消三名总统竞选人的资格 |
| gold-581e6a1a832e | 2012-04-17 | 穆斯林兄弟会推出候补候选人穆尔西 |

预算：

```json
{
  "queries_left": 3,
  "tokens_left": 4000
}
```

初始 Gap Memory：

```json
{
  "phase": "GAP_REFINEMENT",
  "open_gaps": [
    {
      "gap_id": "g001",
      "type": "MISSING_DATE",
      "priority": 1.0,
      "status": "OPEN",
      "window_start": "2012-01-21",
      "window_end": "2012-04-14"
    }
  ],
  "closed_gaps": [],
  "attempted_queries": [],
  "low_gain_streak": 0
}
```

合法 Controller 动作：`SEARCH` 或 `STOP`。

## 3. 标注生成轨迹

### 3.1 第一次 proposal

教师返回的候选 query 数量不满足3–4条约束，程序拒绝该标签：

```text
candidate_queries must contain 3 to 4 queries
```

标注器没有接受错误结果，而是把具体错误反馈给教师并要求返回新的 JSON。

### 3.2 修复后的结构化思考

```json
{
  "search_need": "HIGH",
  "target": "Find the exact date of the inaugural session of Egypt's new parliament after the 2011-2012 election, within the period from 2012-01-21 to 2012-04-14.",
  "reason_code": "MISSING_DATE|TARGET_PARLIAMENT_SESSION|WINDOW_JAN21_APR14"
}
```

这段 thought 的含义是：

1. 当前高优先级日期缺口仍然开放；
2. 目标不是泛搜埃及政治，而是新议会首次会议；
3. query 应限制在当前 gap 的时间窗口内；
4. 因此下一动作应倾向 SEARCH，而不是 STOP。

### 3.3 Memory draft

```json
{
  "active_target": "Date when Egypt's newly elected parliament held its first/inaugural session after the January 2012 election.",
  "reason_code": "MISSING_DATE|TARGET_PARLIAMENT_SESSION|WINDOW_JAN21_APR14"
}
```

### 3.4 候选 SEARCH

教师生成四条候选，程序在同一冻结 Crisis/Egypt BM25 索引中逐条真实执行：

| 排名 | query | exact gain | ±2日 gain | 新日期 | 文档 | 得分 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | Egypt new parliament inaugural session date January 2012 | 1 | 3 | 9 | 12 | 1331 |
| 2 | Egypt parliament first session after 2012 election | 1 | 2 | 10 | 12 | 1233 |
| 3 | Egyptian parliamentary opening session 2012 Morsi era | 0 | 0 | 10 | 12 | 33 |
| 4 | when did Egypt's new parliament convene for first time 2012 | 0 | 0 | 10 | 12 | 33 |

排序不是由教师口头判断决定，而是程序根据冻结检索回放计算：

```text
score =
  1000 × exact_gold_gain
  + 100 × window_2d_gold_gain
  + 2 × new_date_count
  + document_count
  + query_novelty
```

### 3.5 教师偏好复核

冻结回放后，教师给出：

```json
{
  "chosen_action": "SEARCH",
  "rejected_action": "STOP",
  "chosen_query": "Egypt new parliament inaugural session date January 2012",
  "rejected_query": "Egyptian parliamentary opening session 2012 Morsi era",
  "action_reason": "The chosen query directly targets the missing inaugural-session date inside the active window and has strong retrieval evidence; the rejected Morsi-era query is off-target, and stopping would leave the high-priority date gap unresolved."
}
```

偏好分数：

| 决策 | chosen | rejected | margin |
|---|---:|---:|---:|
| QUERY | 0.90 | 0.20 | 0.70 |
| ACTION | 0.95 | 0.10 | 0.85 |
| MEMORY | 0.90 | 0.20 | 0.70 |

### 3.6 Memory 正负例

chosen：

```json
{
  "active_target": "Date of Egypt's newly elected parliament's inaugural session (gap g001)",
  "gap_status": "OPEN",
  "last_query": "Egypt new parliament inaugural session date January 2012"
}
```

rejected：

```json
{
  "active_target": "Morsi-era parliamentary-opening context outside the missing-date window",
  "gap_status": "CLOSED"
}
```

负例的问题是：把尚未解决的日期缺口错误标为 CLOSED，并把检索目标偏移到穆尔西时期。

## 4. 编译后的真实 Controller 动作

Assistant 先输出结构化 thought 和 Memory update，然后调用 SEARCH：

```json
{
  "content": {
    "thought": {
      "search_need": "HIGH",
      "target": "Find the exact date of the inaugural session of Egypt's new parliament after the 2011-2012 election, within the period from 2012-01-21 to 2012-04-14.",
      "reason_code": "MISSING_DATE|TARGET_PARLIAMENT_SESSION|WINDOW_JAN21_APR14"
    },
    "memory_update": {
      "active_target": "Date of Egypt's newly elected parliament's inaugural session (gap g001)",
      "gap_status": "OPEN",
      "last_query": "Egypt new parliament inaugural session date January 2012"
    }
  },
  "tool_call": {
    "name": "SEARCH",
    "arguments": {
      "dataset": "crisis",
      "topic": "egypt",
      "query": "Egypt new parliament inaugural session date January 2012",
      "top_k": 12
    }
  }
}
```

## 5. SEARCH observation

| id | 文档日期 | 标题 | 作用 |
|---|---|---|---|
| 2210 | 2012-01-25 | Thousand of Egyptians gather in Tahrir Square to mark anniversary of uprising | 说明新议会已在星期一召开首次会议 |
| 798 | 2012-01-25 | Egypt 1st anniversary of Hosni Mubarak fall marked by thousands in Cairo's Tahrir Square | 同样指出首次会议在星期一举行 |
| 20 | 2012-01-26 | Egypt's generals laid on a celebration – but the people just wanted to protest | 指出议会星期一召开首次会议 |
| 1296 | 2012-01-23 | Egypt parliament opens to lively first day | 直接给出2012-01-23首次议会会议，命中 accepted date |
| 1982 | 2012-01-25 | Egypt Thousands Gather In Tahrir Square To Mark First Anniversary Of Uprising | 支持星期一首次会议 |
| 3110 | 2012-12-22 | Key events in Egypt's revolution and transition | 讲述7月议会复会，属于后续事件 |
| 2510 | 2012-12-22 | Key events in Egypt's revolution and transition | 与上一条近重复 |
| 4983 | 2012-12-21 | Key events in Egypt's revolution and transition leading up to referendum on draft constitution | 讲述7月议会复会 |
| 5724 | 2011-02-05 | Egypt Why 25 January will be a date forever enshrined in the country's history | 与目标日期缺口弱相关 |
| 831 | 2012-06-19 | In Egypt, a rare second chance for US to support democracy | 议会背景信息 |
| 3929 | 2012-07-11 | Egypt's president vows to respect court ruling on parliament | 后期议会争议 |
| 4151 | 2012-12-06 | Egypts constitutional crisis, explained as a simple timeline | 选举和议会背景信息 |

工具 observation 的控制字段为：

```json
{
  "document_count": 12,
  "next_executor": "VERIFY"
}
```

## 6. 本条轨迹的终止位置

本条数据到此结束：

```text
STATE
  → structured thought
  → memory update
  → SEARCH
  → 12 retrieved documents
  → next_executor = VERIFY
```

尚未真实执行：

```text
VERIFY → MERGE → updated timeline → next Controller decision → STOP/SEARCH
```

因此不能把本条称为“完整时间线生成轨迹”；它是一个完整的单步 Controller 训练样本。后续需让冻结 VERIFY/MERGE 消费文档1296等证据，补入日期，再构造 gap closed 后的 `STOP > SEARCH` 状态。

## 7. 编译出的三组 DPO

### QUERY

```text
chosen:   SEARCH(Egypt new parliament inaugural session date January 2012)
rejected: SEARCH(Egyptian parliamentary opening session 2012 Morsi era)
```

### ACTION

```text
chosen:   SEARCH(best query)
rejected: STOP(timeline is sufficiently covered)
```

### MEMORY

```text
chosen:   SEARCH(best query) + gap remains OPEN + record last_query
rejected: SEARCH(best query) + incorrectly mark gap CLOSED
```

这三组样本共享同一个初始 state，只改变一个局部决策。
