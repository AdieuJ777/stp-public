# E1 双模式等价性实验结果

- 场景数：12
- 等价场景数：12
- 等价率：100.0%
- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：7deb2aa03f32be81f2621caa24cda570c6603349（dirty）
- 时间：2026-11-04T02:36:49.549436+00:00

> 等价标准：最终状态、错误码、指定输出字段、重试次数、工具调用序列；不比较事件顺序、节点名和时间戳。

| 场景 | 说明 | 脚本状态 | 图状态 | 脚本错误 | 图错误 | 等价 | 差异 |
|---|---|---|---|---|---|---|---|
| linear | 三步顺序函数任务 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| branch | 单层条件分支 + 汇合 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| nested_branch | 两层嵌套分支 + 双汇合 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| empty_case | 空分支 case 直接走汇合 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| tool_success | 计算器工具成功调用 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| tool_failure_retry | 工具始终失败，重试一次后任务失败 | FAILED | FAILED | E_TOOL_CALL | E_TOOL_CALL | ✅ |  |
| llm_structured | MockLLM 结构化输出 | SUCCEEDED | SUCCEEDED |  |  | ✅ |  |
| step_timeout | 步骤级超时 | FAILED | FAILED | E_STEP_TIMEOUT | E_STEP_TIMEOUT | ✅ |  |
| task_timeout | 任务级超时 | FAILED | FAILED | E_TASK_TIMEOUT | E_TASK_TIMEOUT | ✅ |  |
| model_limit | 模型调用次数上限 | FAILED | FAILED | E_LIMIT_EXCEEDED | E_LIMIT_EXCEEDED | ✅ |  |
| step_limit | 步数上限（含 branch 计数口径） | FAILED | FAILED | E_LIMIT_EXCEEDED | E_LIMIT_EXCEEDED | ✅ |  |
| cancel | 步骤边界取消 | CANCELLED | CANCELLED | E_CANCELLED | E_CANCELLED | ✅ |  |
