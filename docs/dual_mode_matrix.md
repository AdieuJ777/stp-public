# 双模式 Parity 审计（2026-11-04 更新）

> 审计对象：`script.py`、`graph.py`、`plan.py`、`validation.py`、`executor.py` 与现有测试
> 基线：112 个测试全部通过（`python -m pytest -o addopts="" -q`，7.33s）
> 关联：README 的「核心概念 / 执行语义」与内部 ADR-000
> 性质：2026-11-03 完成只读审计；2026-11-04 补齐 §6 的 1–15 项测试。

---

## 0. 结论摘要

1. **执行语义 parity 由架构保证**：脚本与图生成同一份 `TaskIR`，由同一个 `TaskRuntime` 执行，不存在第二条执行路径。
2. **编排表达力并不对等**：脚本 API 是受限前端（顺序 + 条件分支 + 自动汇合 + 单个 end），图 API 是完整 DAG 前端（显式节点/边/路由、多 end、共享节点、任意无环拓扑）。
3. **差分测试已补齐最小集**：2026-11-04 新增嵌套分支、工具失败/重试、LLM 步骤、空 case、取消、单层/嵌套分支恢复、步骤/任务超时、模型调用/步数限制的 parity，以及多 end、共享节点、多 end 恢复 3 个图模式回归用例。仍缺非幂等副作用和模型失败的 parity。
4. **建议把 parity 拆成五层分别验收**（见 §2），并在文档中明确“脚本表达力边界”，而不是追求所有拓扑在两端都能写出来。

一句话：

> **在“两端都能表达的计划形状”内，行为等价是架构保证的；最小差分测试集已经跑通；“哪些形状只能图模式表达”已有清单，剩余缺口集中在副作用/模型失败。**

---

## 1. 审计方法

- 静态阅读：`stp/script.py`、`stp/graph.py`、`stp/plan.py`、`stp/validation.py`、`stp/executor.py`。
- 测试清点：`tests/test_equivalence.py`、`tests/test_executor.py`、`tests/test_checkpoint.py` 中所有使用 `GraphBuilder` / `GraphTask` 的用例。
- 运行测试：112 passed。
- 不在本次范围：checkpoint 内部字段与 P1 需求逐条对照（见 `p1_evidence_recheck.md`）。

---

## 2. Parity 的五层定义

| 层级 | 含义 | 当前判断 |
|---|---|---|
| P-A 计划汇聚 | 两端都生成同一 `TaskIR` schema，走同一套静态校验 | ✅ 满足 |
| P-B 执行语义 | 相同输入 + 相同执行路径 → 相同状态、输出、错误码、调用次数 | ✅ 满足（同一 Runtime） |
| P-C 可观测 | 事件类型、`seq`、错误码、checkpoint 字段一致 | ✅ 满足（同一 EventBus / Store） |
| P-D 作者表达力 | 同一逻辑任务能否在两端都写出来 | ⚠️ 不对等，且边界未文档化 |
| P-E 差分证据 | 两端写同一任务并比较关键行为 | ✅ 覆盖 §6 最小集；仍缺超时/限制/非线性恢复 |

**关键区分**：P-D 不要求“脚本能表达所有 DAG”（那会变成“任意 Python 转图”）；它要求把“脚本能表达什么、不能表达什么”写成明确清单，并在重叠范围内用 P-E 证明等价。

---

## 3. 能力矩阵

图例：✅ 支持；⚠️ 支持但未测试/有差异；❌ 不支持；— 不适用。

| # | 能力 | 脚本 API | 图 API | 运行时共享 | 差分测试 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 顺序步骤（函数） | ✅ `ctx.step(func=)` | ✅ `add_step(func=)` | ✅ | ✅ `test_script_and_graph_are_equivalent` | 线性链路 |
| 2 | 顺序步骤（工具） | ✅ `ctx.step(tool=)` | ✅ `add_step(tool=)` | ✅ | ✅ `test_tool_failure_and_retry_parity` | 工具失败/重试路径 |
| 3 | 顺序步骤（LLM） | ✅ `ctx.step(llm=)` | ✅ `add_step(llm=)` | ✅ | ✅ `test_llm_step_parity` | MockLLM 结构化输出 |
| 4 | 输入映射与 `Ref` 解析 | ✅ | ✅ | ✅ | ✅ | 同一 `resolve()` |
| 5 | `output_key` / `output_schema` | ✅ | ✅ | ✅ | ⚠️ | 差分用例未断言 output_schema |
| 6 | 步骤级 `timeout_seconds` | ✅ | ✅ | ✅ | ✅ `test_step_timeout_parity` | `E_STEP_TIMEOUT` 一致 |
| 7 | 条件分支 | ✅ `ctx.branch()` | ✅ `add_branch()` + `add_route()` | ✅ | ✅ | 单层分支 |
| 8 | 自动生成 join | ✅ 自动 | ❌ 需手工 `add_join()` | ✅ | — | 同一逻辑任务 IR 形状不同 |
| 9 | 手工 join / 多入边 | ❌ 不可表达 | ✅ | ✅ | ❌ | 图模式独有能力 |
| 10 | 嵌套分支 | ✅ 可写 | ✅ 可写 | ✅ | ✅ `test_nested_branch_parity` | 两层 branch + 双 join |
| 11 | 空分支 case | ✅ 路线直接指向 join | ✅ 直接连到 join | ✅ | ✅ `test_empty_branch_case_parity` | 空 case 直接走 join |
| 12 | 分支 case 内调用 end | ❌ 构建期拒绝 | ✅ 可直接路由到 end | ✅ | — | 脚本侧有意限制 |
| 13 | 多个 end 节点 | ❌ 只允许一个 | ✅ 可多个 | ✅ | ✅ `test_graph_multiple_ends_selects_active_end` | 图模式独有；两个 end 都验证 |
| 14 | 共享节点 / 菱形拓扑 | ❌ 线性 cursor 不可表达 | ✅ | ✅ | ✅ `test_graph_shared_node_diamond_runs_once` | 两条入边、只执行一次 |
| 15 | 任意无环 DAG | ❌ | ✅ | ✅ | ❌ | 图模式独有能力 |
| 16 | 显式入口 / 显式边 | ❌ 隐式 | ✅ `set_entry` / `add_edge` | ✅ | — | 作者体验差异 |
| 17 | `end` 默认输出映射 | ✅ | ✅ | ✅ | ⚠️ | 都从 output_schema 推导 |
| 18 | 输入/输出契约校验 | ✅（同一 spec） | ✅（同一 spec） | ✅ | ✅ | 同一校验器 |
| 19 | 静态计划校验 | ✅ 同一 `validate_plan()` | ✅ 同一 `validate_plan()` | ✅ | ✅ | 构建期报错时机不同 |
| 20 | 重试与指数退避 | ✅（共享） | ✅（共享） | ✅ | ✅ `test_tool_failure_and_retry_parity` | 重试次数 + 最终错误码 |
| 21 | 步数/模型调用/任务超时限制 | ✅（共享） | ✅（共享） | ✅ | ✅ `test_task_timeout_parity`、`test_model_call_limit_parity`、`test_max_steps_limit_parity` | 三种限制均一致 |
| 22 | 取消 | ✅（共享） | ✅（共享） | ✅ | ✅ `test_cancellation_parity` | 步骤边界取消 |
| 23 | 事件与错误码 | ✅（共享） | ✅（共享） | ✅ | ⚠️ 间接覆盖 | `compare_runs` 比较错误码/重试/工具序列，不比较事件顺序 |
| 24 | 检查点与恢复 | ✅ `ScriptTask.resume()` | ✅ `GraphTask.resume()` | ✅ | ✅ `test_branch_resume_parity`、`test_nested_branch_resume_parity`、`test_graph_multiple_ends_resume` | 线性 + 单层/嵌套分支 + 图模式多 end |
| 25 | 非幂等副作用保护 | ✅（共享） | ✅（共享） | ✅ | ❌ | 仅单模式测试 |
| 26 | 暂停 / 人工审批 | ❌ | ❌ | ❌ | — | P2，`PauseToken` 占位 |
| 27 | 循环 / 并行 | ❌ | ❌ | ❌ | — | P2，静态校验拒绝成环 |

---

## 4. 差异分类

### A. 架构允许、需要文档化的表达力差异

这些不是缺陷，但不能继续隐含在实现里：

- 脚本是“顺序 + 分支 + 自动汇合 + 单 end”的受限前端；
- 图形 API 可以表达多 end、共享节点、菱形、任意无环 DAG；
- 脚本分支会自动生成 `join`，图模式不一定有 `join`，因此“同一逻辑任务”的 IR 形状可能不同；
- 脚本禁止在分支 case 内调用 `end()`，图模式允许分支直接路由到 end。

**处理方式**：在本文件与 ADR-000 中声明“parity 以可观察行为为准，不以 IR 形状为准”；把图模式独有能力单独列出，不要求脚本补齐。

### B. 仍缺少差分证据的差异

以下能力在两端都已实现，但还没有 parity 用例：

- 非幂等副作用保护的跨模式对比；
- 模型失败、结构化输出失败的跨模式对比。

已补充（2026-11-04）：工具步骤、LLM 步骤、嵌套分支、空 case、工具失败/重试、取消、单层/嵌套分支恢复、步骤/任务超时、模型调用/步数限制、图模式多 end 恢复。

### C. 需要决策的差异

- 图模式独有的控制流（多 end、共享节点、菱形）是否需要脚本等价物？当前答案是“不需要”，但必须在文档里写死。
- 脚本是否应该提供 `ctx.join()` / 多 end 支持？在 P1/P2 范围内建议不做，避免脚本 API 变成半个图 API。

---

## 5. 缺口清单

| 编号 | 缺口 | 状态 |
|---|---|---|
| G-01 | 嵌套分支 parity | ✅ `test_nested_branch_parity` |
| G-02 | 多 end | ✅ `test_graph_multiple_ends_selects_active_end` |
| G-03 | 空分支 case | ✅ `test_empty_branch_case_parity` |
| G-04 | 工具失败/重试 parity | ✅ `test_tool_failure_and_retry_parity` |
| G-05 | 取消 / 超时 parity | ✅ `test_cancellation_parity`、`test_step_timeout_parity`、`test_task_timeout_parity` |
| G-06 | 工具 / LLM 步骤 parity | ✅ `test_tool_failure_and_retry_parity`、`test_llm_step_parity` |
| G-07 | 脚本表达边界未文档化 | ✅ 本文档 §3/§4 + README 限制 |
| G-08 | `compare_runs` 不比较事件顺序/节点名 | ⚠️ 有意的设计；需在 traceability/论文中写明比较维度 |
| G-09 | 嵌套 / 多 end 恢复 | ✅ `test_nested_branch_resume_parity`、`test_graph_multiple_ends_resume` |

---

## 6. 已补充的最小测试集（2026-11-04）

| # | 测试 | 文件 | 验证内容 |
|---|---|---|---|
| 1 | `test_nested_branch_parity` | `tests/test_parity.py` | 两层 branch + 双 join，比较输出/状态/调用行为 |
| 2 | `test_tool_failure_and_retry_parity` | `tests/test_parity.py` | `E_TOOL_CALL`、重试次数、工具调用序列 |
| 3 | `test_llm_step_parity` | `tests/test_parity.py` | MockLLM 结构化输出、`LLM_CALL_END` 次数 |
| 4 | `test_empty_branch_case_parity` | `tests/test_parity.py` | 空 case 直接走 join，work 分支不执行 |
| 5 | `test_cancellation_parity` | `tests/test_parity.py` | `E_CANCELLED`、第二步未启动 |
| 6 | `test_graph_multiple_ends_selects_active_end` | `tests/test_graph_only.py` | 图模式多 end，活跃 end 正确 |
| 7 | `test_graph_shared_node_diamond_runs_once` | `tests/test_graph_only.py` | 共享节点两条入边，只执行一次 |
| 8 | `test_branch_resume_parity` | `tests/test_parity.py` | 单层分支恢复：脚本/图都跳过 classify+route，重跑 high，输出一致 |
| 9 | `test_graph_branch_resume_route_mismatch` | `tests/test_parity.py` | 恢复时分支路由变化 → `E_RESUME_UNSUPPORTED` |
| 10 | `test_step_timeout_parity` | `tests/test_parity_limits.py` | 步骤超时 `E_STEP_TIMEOUT` |
| 11 | `test_task_timeout_parity` | `tests/test_parity_limits.py` | 任务超时 `E_TASK_TIMEOUT` |
| 12 | `test_model_call_limit_parity` | `tests/test_parity_limits.py` | 模型调用上限 `E_LIMIT_EXCEEDED`，只调用 1 次 |
| 13 | `test_max_steps_limit_parity` | `tests/test_parity_limits.py` | 步数上限 `E_LIMIT_EXCEEDED`，含 branch 计数口径 |
| 14 | `test_nested_branch_resume_parity` | `tests/test_parity_nested_resume.py` | 外层+内层 branch 恢复，跳过 classify/outer/inner |
| 15 | `test_graph_multiple_ends_resume` | `tests/test_graph_only.py` | 图模式多 end 恢复，只执行活跃 end |

> 第 6、7、15 项不是 parity 测试，而是“图模式独有能力”的回归测试，单独放在 `test_graph_only.py`。

剩余建议补充：

1. 非幂等副作用保护的跨模式对比；
2. 模型失败 / 结构化输出失败的跨模式对比。

---

## 7. 结论

- **P-A/P-B/P-C 已经成立**，这是项目最重要的资产：两端共享 IR 和运行时。
- **P-D 的边界必须在文档里说清楚**：脚本不是图的全集，图模式独有能力不等于“脚本模式缺失”。
- **P-E 的最小集已经跑通**：嵌套分支、工具失败/重试、LLM、空 case、取消、单层/嵌套分支恢复、步骤/任务超时、模型调用/步数限制都有脚本/图对比；剩余缺口是非幂等副作用和模型失败。
- 0.3.0 的 parity 门槛（§6 的 1–5 项通过 + 矩阵随代码同步）已经满足；0.4.0 前建议补完“剩余建议”中的 3、4 两项。

---

## 8. 维护方式

- 新增任何控制流/能力时，先在本矩阵加一行，标明脚本/图/运行时/差分测试四列；
- 若只支持单模式，必须在“备注”写 `experimental` 并给出理由；
- 每次发版前跑一遍 §6 的测试集；
- 本文件是 ADR-000 的执行清单，不是一次性报告。
