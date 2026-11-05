# E6 场景规格与公平性约定

## T1：审批/记账正常路径

输入：

```json
{"text": "机票 1200 元（差旅）"}
```

流程：

```text
extract_expense → check_budget → branch
    ├─ approval: request_approval
    └─ record:   record_expense
    → final output
```

期望输出：

```json
{
  "status": "pending_approval",
  "expense": {
    "amount": 1200.0,
    "category": "travel",
    "vendor": "Airline",
    "description": "机票 1200 元（差旅）"
  },
  "result": {"status": "pending_approval", "request_id": "APR-1"}
}
```

实现：

- STP 脚本：`stp_t1_script.py`
- STP 图：`stp_t1_graph.py`
- LangGraph：`langgraph_t1.py`

## T2：失败 + 恢复

流程：

```text
s1 → s2 → s3 → s4 → s5
```

- `s3` 第一次调用抛 `RuntimeError`；
- 完成后输出 `{"value": 5}`；
- 恢复后 `s1`、`s2` 不得重复执行，`s3` 重试一次。

实现：

- STP：`stp_t2.py`（`JsonCheckpointStore`）
- LangGraph：`langgraph_t2.py`（`SqliteSaver`）

## 公平性约定

1. 两个任务都只使用确定性函数；不使用真实 LLM。
2. 业务函数（抽取、预算、审批、记账、步骤函数）放在 `shared.py`，两套实现共用，不计入编排 LOC。
3. LOC 口径：非空、非纯注释行；docstring 计入。
4. 恢复开销代码都计入：
   - STP：`JsonCheckpointStore`、`resume()`；
   - LangGraph：`SqliteSaver`、`thread_id` / `configurable`、第二次 `invoke(None, config)`。
5. 只比较重叠能力；不比较循环、并行、多 Agent、流式、生态。
6. 单开发者、小任务；开发时间只做案例记录，不作为严谨指标。
7. LangGraph 版本固定为 `1.2.20`，`langgraph-checkpoint-sqlite` 固定为 `3.1.3`。
