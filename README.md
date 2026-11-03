# STP

**STP（Structured Task Protocol）** 是一个面向结构化任务协议的双模式智能体编排框架：脚本 API 和图 API 生成同一份 TaskIR，由同一个可恢复、可审计的运行时执行。

本仓库实现 **P0 核心范围 + P1 检查点/恢复**：结构化任务契约、轻量 TaskIR、脚本/图双前端、统一执行器、静态校验、错误分类与重试、执行限制、事件日志、双模式差分比较、JSON 检查点与失败恢复，以及记账 Agent 示例。MockLLM 仅用于测试层，不进入 Agent 应用。

> 当前定位是 **research prototype（研究原型）**，不是生产级框架。执行回放、循环、并行、分布式等能力尚未实现，详见文末“范围与限制”。

---

## 快速开始

要求 Python 3.11+，推荐使用 `uv`：

```bash
cd stp
uv sync --extra dev
uv run pytest -q

# Demo 只使用本地 Ollama 的 qwen3:8b（请先 ollama serve 并 pull 模型）
uv run stp-demo
uv run python -m stp.demo.accounting
uv run python stp/demo/accounting.py
```

Demo 不会回退到 MockLLM。模型不可用时会明确失败；离线验证请运行 `uv run pytest`（测试层使用 MockLLM）。

示例输出（金额 1200、差旅预算 500，因此走审批）：

```json
{
  "status": "SUCCEEDED",
  "output": {
    "status": "pending_approval",
    "expense": {"amount": 1200, "category": "travel", "vendor": "Airline"},
    "result": {"status": "pending_approval", "request_id": "APR-..."}
  }
}
```

### 检查点与恢复（P1）

```python
import asyncio

from stp import JsonCheckpointStore, TaskRuntime

store = JsonCheckpointStore(".checkpoints")
runtime = TaskRuntime(tools=tools, checkpoint_store=store)
run = asyncio.run(task_obj.run({"text": "机票 1200 元"}, runtime=runtime))

if run.status.value == "FAILED":
    # 从该 run 的最近检查点恢复；恢复会创建一个新的 TaskRun，
    # resumed_from_run_id 指向原始 run。
    resumed = asyncio.run(
        task_obj.resume(run_id=run.run_id, runtime=runtime)
    )
    print(resumed.status, resumed.output, resumed.resumed_from_run_id)
```

也可以使用 `runtime.resume(...)` 或指定 `checkpoint_id`；Demo CLI 支持：

```bash
uv run stp-demo --checkpoint-dir .checkpoints "机票 1200 元"
# 第一次失败后
uv run stp-demo --checkpoint-dir .checkpoints --resume-run <run_id>
```

恢复语义：

- 每个 step / branch 成功后保存检查点；失败时额外保存“失败现场”；
- 恢复时从 entry 重新遍历计划，已完成节点只发 `STEP_SKIPPED`，不重复调用工具/模型；
- 已保存的分支选择会被校验，条件在恢复时给出不同路由则拒绝恢复；
- `step_count` / `model_call_count` 从检查点恢复，执行限制不会因恢复而重置；
- 非幂等副作用工具结果 `UNKNOWN` 时拒绝自动恢复，返回 `E_RESUME_UNSUPPORTED`；
- 检查点保存失败不会重试已经成功的步骤，任务以 `E_CHECKPOINT_IO` 失败。

---

## 目录结构

```
stp/
  stp/
    models.py        TaskSpec / TaskRun / ExecutionEvent / ToolSpec / 状态机
    errors.py        统一错误码与异常体系
    refs.py          构建期符号引用（InputRef / StateRef / StepRef）
    plan.py          PlanNode / TaskIR / PlanBuilder
    script.py        声明式脚本 API（@task / ctx.step / ctx.branch / ctx.end）
    graph.py         显式图 API（节点、边、路由、汇合）
    validation.py    执行计划静态校验
    context.py       ExecutionContext：状态、快照、事件、工具/模型调用
    executor.py      统一执行器：图遍历、重试、限制、取消、检查点、恢复
    checkpoint.py    Checkpoint / CheckpointStore / JsonCheckpointStore
    redaction.py     事件日志敏感字段脱敏
    llm.py           LLMBackend / MockLLM / OpenAICompatBackend
    tools.py         ToolSpec / ToolRegistry / CalculatorTool
    events.py        EventBus
    stores.py        InMemory / JSONL / SQLite 事件存储
    diff.py          双模式可观察结果比较
    demo/accounting.py  记账 Agent 示例
  docs/p1_traceability.md  FR/AT/NFR 需求追溯与设计偏差
  docs/p1_acceptance_report.md  P1 验收结果与最小基准
  docs/p1_evidence_recheck.md   P1 需求证据链独立复核
  docs/dual_mode_matrix.md      脚本/图双模式 parity 审计
  docs/0.3.0_backlog.md         0.3.0 可信执行债务与优先级
  bench/p1_benchmark.py    NFR-007 最小基准脚本
  tests/             97 个 pytest 用例
```

P1 需求与验收编号的对应关系见 [`docs/p1_traceability.md`](docs/p1_traceability.md)，
当前测试与基准结果见 [`docs/p1_acceptance_report.md`](docs/p1_acceptance_report.md)，
双模式能力覆盖见 [`docs/dual_mode_matrix.md`](docs/dual_mode_matrix.md)，
P1 证据链复核见 [`docs/p1_evidence_recheck.md`](docs/p1_evidence_recheck.md)，
下一阶段债务清单见 [`docs/0.3.0_backlog.md`](docs/0.3.0_backlog.md)。

> `docs/p1_*` 引用内部需求基线《需求规格说明书_p1阶段.docx》，该基线不随仓库发布。
> 许可证：MIT（见 [`LICENSE`](LICENSE)）。

---

## 核心概念

### TaskSpec

`TaskSpec` 描述“要做什么”，不保存某次执行的状态。包含：

- `input_schema` / `output_schema`：JSON Schema；
- `protocol_version`；
- `constraints`、`retry_policy`、`termination_condition`；
- `max_steps`、`timeout_seconds`、`max_model_calls`；
- 可选 `validators`（业务规则验证器）。

### TaskIR

两种前端最终都生成同一份 `TaskIR`：

- `step`：执行工具 / LLM / 普通函数；
- `branch`：按运行期条件选择唯一路由；
- `join`：分支汇合的结构节点，不执行业务动作；
- `end`：产出最终输出。

第一版只支持有向无环的“顺序 + 分支 + 汇合”结构。

### 执行语义

- 输入在任务开始前校验，输出在 `end` 节点校验；
- 静态校验在执行前检查悬空引用、成环、不可达节点、分支缺省、output_key 冲突、输出映射缺字段、`max_steps` 下限；
- 重试由运行时在步骤级统一管理，工具和 LLM 适配器不得自行重试；
- 有副作用且非幂等的工具拒绝自动重试，返回 `W_NON_IDEMPOTENT`；
- 超时、步数、模型调用次数由运行时强制；
- 取消在步骤边界生效；任务超时/取消时，未收到结果的工具调用会补记 `UNKNOWN`。

### P1 检查点

检查点只保存任务数据，不保存执行器、后端客户端、协程等运行时对象：

```text
checkpoint_version / checkpoint_id / sequence
run_id / lineage_id / task_id / task_version / protocol_version / status
plan_hash / spec_hash / content_hash
input / state / step_results / completed_steps / branch_routes
step_count / model_call_count / attempt_count / next_step_id
elapsed_seconds / created_at
error / unknown_tool_calls / resume_safe / resume_block_reason
```

- `completed_steps` 记录真正执行过的 step/branch 节点；`join`/`end` 是结构节点，不参与跳过判定。
- `step_results` 是由 `state` 与 completed step 派生的只读视图；`state` 是唯一事实来源。
- `task_version` 是 `spec_hash` 的前 16 位，属于内容派生版本；完整 `spec_hash` 用于兼容校验。
- `status` 记录保存检查点时的任务状态；失败现场通常为 `FAILED` 或 `CANCELLED`。
- `content_hash` 覆盖检查点内容，能识别“合法 JSON 内被篡改”的情况。
- `plan_hash` / `spec_hash` 用于拒绝“计划或任务定义已变化”的检查点；它们是结构哈希，不包含函数体。
- `elapsed_seconds` 用于跨恢复累计任务执行时间；恢复时只使用剩余超时预算。
- 写入采用临时文件 + `fsync` + `os.replace`，读取时校验 JSON、schema、版本和内容哈希。
- 恢复会创建新的 `TaskRun`，并通过 `resumed_from_run_id` 保留与原始运行的关系。
- 恢复前会校验 `completed_steps` 是否位于同一条实际执行路径上；冲突路径会被拒绝。
- 检查点默认使用 AES-GCM 加密落盘：密钥优先取环境变量 `STP_CHECKPOINT_KEY`，
  其次使用 `JsonCheckpointStore(..., encryption_key=...)`；都没有时在 `base_dir`
  生成权限为 0600 的 `.checkpoint.key`。环境变量应使用高熵随机值，例如
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`。
- 旧版本写入的明文检查点仍可读取；新写入默认加密。`encrypt=False` 仅用于调试或迁移，
  会在磁盘上留下明文。
- 工具调用的 `idempotency_key` 形如 `lineage_id:node_id:step_index`，跨重试和恢复稳定；
  `call_id` 只用于单次调用的诊断与事件关联。

---

## 脚本模式

```python
import asyncio

from stp import CalculatorTool, TaskRuntime, TaskSpec, ToolRegistry, task

spec = TaskSpec(
    task_id="TSK-DEMO",
    name="calculator_demo",
    input_schema={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    output_schema={
        "type": "object",
        "properties": {"sum": {"type": "number"}},
        "required": ["sum"],
    },
)


@task(spec)
def build(ctx):
    ctx.step(
        "add",
        tool="calculator",
        input={"expression": "2 + 3"},
        output_key="sum_raw",
    )
    ctx.end({"sum": ctx.state["sum_raw"]["result"]})


runtime = TaskRuntime(tools=ToolRegistry([CalculatorTool()]))
run = asyncio.run(build.run({"a": 1, "b": 2}, runtime=runtime))
print(run.status, run.output)
```

注意：脚本函数是**构建期**执行的，`ctx.step()` 不会立刻调用工具，而是返回 `StepRef`；运行期分支必须用 `ctx.branch()`，不能依赖 `StepRef` 做 Python `if` 判断。

分支写法：

```python
@task(spec)
def build(ctx):
    ctx.step("classify", func=classify, input={"text": ctx.input["text"]}, output_key="expense")

    def approval_case(c):
        c.step("request_approval", tool="request_approval", input={...}, output_key="result")

    def record_case(c):
        c.step("record_expense", tool="record_expense", input={...}, output_key="result")

    ctx.branch(
        "route",
        condition=lambda state: "approval" if state["expense"]["amount"] > 1000 else "record",
        cases={"approval": approval_case, "record": record_case},
        default="record",
    )
    ctx.end({"status": ctx.state["result"]["status"]})
```

`ctx.branch()` 会自动生成汇合节点；分支 case 内不要调用 `ctx.end()`，在汇合之后统一调用。

---

## 图模式

```python
from stp import GraphBuilder, GraphTask, TaskRuntime
from stp.refs import Ref

builder = GraphBuilder(spec)
classify = builder.add_step("classify", func=classify, input={}, output_key="expense")
route = builder.add_branch("route", condition=condition, default="low")
high = builder.add_step("high", func=high_handler, input={}, output_key="result")
low = builder.add_step("low", func=low_handler, input={}, output_key="result")
join = builder.add_join("join")
end = builder.add_end("end", {"status": Ref("state", "result", ("status",))})

builder.set_entry(classify)
builder.add_edge(classify, route)
builder.add_route(route, "high", high)
builder.add_route(route, "low", low)
builder.add_edge(high, join)
builder.add_edge(low, join)
builder.add_edge(join, end)

run = asyncio.run(GraphTask(spec, builder).run({}, runtime=TaskRuntime()))
```

---

## LLM 后端

### MockLLM（仅测试层）

`MockLLM` 是测试替身，**不进入 Agent 应用，也不作为 Demo 的默认或回退后端**。它只用于 pytest 中验证框架机制：

```python
from stp import MockLLM, TaskRuntime

runtime = TaskRuntime(backend=MockLLM([{"value": 7}]))
```

`MockLLM` 支持：

- 列表：按调用顺序消费；
- 字典：作为固定结构化结果；
- 字符串：作为文本结果；
- `Exception` 实例：直接抛出，用于测试重试/失败；
- `{"__sleep__": 0.2, "response": ...}`：模拟慢调用。

Demo 层只接受真实 backend；如果需要在测试中复用 accounting 任务，直接把 `MockLLM` 作为 `backend=` 注入 `run_demo()`，而不是让应用层自己创建 Mock。

### 本地 Ollama（Demo 默认后端）

Demo 默认连接：

```text
base_url = http://localhost:11434/v1
model    = qwen3:8b
api_key  = ollama
```

先启动 Ollama 并准备模型：

```bash
ollama serve
ollama pull qwen3:8b
uv run stp-demo
```

需要换模型或地址时，设置环境变量：

```bash
export STP_LLM_BASE_URL=http://localhost:11434/v1
export STP_LLM_API_KEY=ollama
export STP_LLM_MODEL=qwen2.5:7b-instruct
export STP_LLM_TIMEOUT=120
```

适配器不会自行重试业务调用；`json_schema` 不被支持时会回退到 `json_object`，之后仍由 JSON Schema 二次校验。模型输出中的 markdown 代码围栏和前后解释文字会被剥离，再解析 JSON。

---

## 事件与差分比较

```python
from stp import JsonlEventStore, TaskRuntime

runtime = TaskRuntime(event_store=JsonlEventStore("events.jsonl"))
run = asyncio.run(task_obj.run({...}, runtime=runtime))

for event in runtime.events_for(run.run_id):
    print(event.seq, event.event_type.value, event.payload)

# 兼容层：last_events 仍表示“最近一次 run”，并发场景请使用 events_for(run_id)。
for event in runtime.last_events:
    print(event.seq, event.event_type.value, event.payload)
```

事件包含 `run_id`、`task_id`、`seq`、`step_index`、`step_id`、`attempt`、`event_type`、`payload`，以及
`event_schema_version` 和可选的 `duration_ms`。任务级事件的 `step_id` 为 `None`。
事件 payload 会经过轻量脱敏：常见的 `api_key`、`password`、`authorization` 等字段以及
明显的 `sk-...` / `Bearer ...` 文本会被替换为 `***REDACTED***`。

关键 payload 字段：

- `STEP_END`：`output_key`、`input_hash`、`output_hash`；
- `STEP_SKIPPED`：恢复时被跳过的已完成节点（`node_id`、`output_key` / `selected_route`）；
- `LLM_CALL_END`：`model_name`、`token_usage`；
- `TOOL_CALL_END`：`status`；取消/超时未收到结果时为 `UNKNOWN`；
- `ERROR_OCCURRED`：`error_code`、`retryable`、`attempt`；
- `RETRY_SCHEDULED`：`attempt`、`next_attempt`、`delay_seconds`；
- `CHECKPOINT_SAVED`：`checkpoint_id`、`sequence`、`resume_safe`；
- `CHECKPOINT_LOADED` / `TASK_RESUMED`：`checkpoint_id`、`resumed_from_run_id`；
- `RECOVERY_FAILED`：恢复预检查失败时的 `error_code`、`checkpoint_id`。

事件 sink（JSONL / SQLite / 自定义 sink）写入失败不会被静默忽略：运行时继续执行，
但在任务结束时会以 `E_EVENT_LOG` 失败，避免“业务成功、日志失败却仍报告 SUCCEEDED”。

双模式比较：

```python
from stp.diff import compare_runs

report = compare_runs(
    script_run,
    graph_run,
    left_events=script_runtime.last_events,
    right_events=graph_runtime.last_events,
    strict_fields=["status"],
)
print(report.equivalent, report.differences)
```

比较的是最终状态、错误码、关键输出字段、重试次数和工具调用序列，不比较事件顺序、节点名称和时间戳。

---

## 错误码

| error_code | 含义 |
|---|---|
| `E_CONTRACT_INPUT` | 任务/工具输入不符合 Schema |
| `E_CONTRACT_OUTPUT` | 任务/工具/步骤输出不符合 Schema |
| `E_PLAN_INVALID` | 计划非法或脚本使用了不受支持的构造 |
| `E_STATE_INVALID` | 非法状态转换 |
| `E_STEP_TIMEOUT` | 步骤级超时 |
| `E_TASK_TIMEOUT` | 任务级超时 |
| `E_LIMIT_EXCEEDED` | 步数或模型调用次数超限 |
| `E_MODEL_CALL` | 模型调用失败 |
| `E_STRUCTURED_OUTPUT` | 结构化输出无效 |
| `E_TOOL_CALL` | 工具调用失败 |
| `W_NON_IDEMPOTENT` | 非幂等副作用工具拒绝重试 |
| `E_BUSINESS_RULE` | 业务规则验证器失败 |
| `E_CANCELLED` | 任务被取消 |
| `E_TERMINATION` | 终止条件不满足 |
| `E_STATE_SERIALIZATION` | `ctx.state` / 输入无法做无损 JSON 往返 |
| `E_CHECKPOINT` | 检查点错误基类 |
| `E_CHECKPOINT_NOT_FOUND` | 指定的检查点不存在 |
| `E_CHECKPOINT_INVALID` | 检查点文件损坏或字段非法 |
| `E_CHECKPOINT_INCOMPATIBLE` | task_id / plan_hash / spec_hash / 输入不匹配 |
| `E_CHECKPOINT_IO` | 检查点读写失败（不可重试） |
| `E_RESUME_UNSUPPORTED` | 检查点存在不安全副作用或分支不可重放，拒绝恢复 |
| `E_EVENT_LOG` | 事件 sink 写入失败；任务不会被标记为成功 |

---

## P1 最小基准

`bench/p1_benchmark.py` 是确定性、无模型依赖的检查点/恢复测量脚本：

```bash
python bench/p1_benchmark.py
```

当前环境（Windows、Python 3.11、15 次中位数）的一次样例结果：

| steps | 无检查点 ms | 有检查点 ms | 额外开销 ms | 检查点字节 |
|---:|---:|---:|---:|---:|
| 5 | 2.07 | 11.67 | 9.59 | 8948 |
| 10 | 2.96 | 21.36 | 18.39 | 20760 |
| 20 | 4.70 | 42.71 | 38.01 | 53716 |

恢复测试中已完成步骤的重复调用数为 **0**。这些数字只用于报告量级，不是性能承诺。
检查点默认加密，字节数与耗时包含 AES-GCM 信封开销；同步 `func` 步骤经线程池执行，
基线也包含 offload 开销。这是安全/响应性与性能的有意取舍。

---

## 范围与限制

### 已实现（P0 + P1 检查点/恢复）

- TaskSpec / TaskRun / ExecutionEvent / ToolSpec；
- 轻量 TaskIR + 脚本/图双前端；
- 静态校验（结构、可达性、环、分支、输出键、输出映射、max_steps 下限）；
- 输入/输出 JSON Schema 校验；
- 错误分类、重试、退避、非幂等副作用保护；
- 工具幂等键跨重试/恢复稳定（`lineage_id:node_id:step_index`）；
- 步数/超时/模型调用次数限制；
- 取消（步骤边界生效）；
- 同步函数与 `FunctionTool` 在线程池执行，不阻塞事件循环；
- 事件按 run 隔离：`events_for(run_id)` 读取指定运行的事件，`last_events` 仅作兼容层；
- 事件日志（内存 / JSONL / SQLite），包含 `task_id`、`step_id`、耗时、schema 版本和轻量脱敏；
- JSON 检查点、失败现场保存、按 run 或 checkpoint_id 恢复；
- 检查点默认 AES-GCM 加密，密钥来自 `STP_CHECKPOINT_KEY` 或本地 0600 key 文件；
- 检查点内容哈希、`task_version`、`step_results` 派生视图与执行耗时预算；
- 恢复时跳过已完成 step/branch、校验分支选择、路径一致性和执行计数器；
- 恢复预检查失败事件（`RECOVERY_FAILED`）；
- 非幂等副作用 `UNKNOWN` 的恢复保护；
- 事件 sink 失败统一以 `E_EVENT_LOG` 失败，不产生假成功；
- 随机 DAG 恢复测试（调用次数 + 输出一致性）；
- MockLLM（仅测试层）+ 双模式差分比较；
- OpenAI 兼容后端适配器（默认连接本地 Ollama）；
- 记账 Agent 示例（含检查点恢复 CLI）；
- 97 个 pytest 用例（全部使用 MockLLM，不依赖 Ollama）。

### 未实现（P1 剩余 / P2）

- 显式暂停与人工审批恢复（P1，`PauseToken` 目前只是占位）；
- 执行回放 / Trace Replay（P1）；
- 检查点迁移器与多版本兼容读取（当前只支持 v1）；
- 循环节点、并行节点、并发状态合并（P2）；
- 分布式调度、多租户、细粒度权限（P2）；
- 可视化调试面板（P2）。

### 已知限制

- 脚本构建函数必须是同步且无副作用的；任意 Python 控制流不会自动转成图；
- 分支 case 内不能调用 `ctx.end()`，需要在汇合后统一结束；
- 互斥分支允许复用同名 `output_key`；同一路径上的重复输出键会被静态校验拒绝；
- 相邻步骤的 Schema 兼容性只做结构级尽力检查，不做完整类型推导；
- `ctx.state` 和任务输入必须是可无损 JSON 往返的值（不能有 tuple、set、非字符串键、自定义对象）；
- `plan_hash` / `spec_hash` 是结构哈希，不检测函数体或验证器代码内部的变化；恢复时应固定代码版本；
- 恢复按“从 entry 重放遍历 + 跳过已完成节点”实现，因此分支条件函数必须确定性、无副作用；条件路由变化会被拒绝；
- 检查点只恢复任务状态，不恢复外部工具/业务系统的状态；有副作用的工具仍需自行实现幂等；
- `func=` 步骤被框架视为可安全重放；有副作用或不可重复执行的逻辑应封装为 `Tool` 并声明 `side_effect` / `idempotent`；
- 同步函数在线程池中执行；步骤/任务超时后线程无法被强制终止，可能仍在后台完成，有副作用的同步逻辑应优先改为 async 工具或幂等工具；
- 检查点保存失败会导致任务失败（`E_CHECKPOINT_IO`），不会重试已成功的步骤；若失败现场也无法写入，自动恢复只能回到更早的检查点，可能重复最后一个已成功步骤，有非幂等副作用时应人工处理；
- 恢复后的超时按检查点累计的**执行耗时**计算剩余预算；进程停机时间不计入，这不是墙钟时间意义上的总预算；
- 事件脱敏是启发式的，只覆盖常见凭据字段和明显的密钥文本；业务字段中的秘密仍需工具作者避免写入；
- 检查点默认加密，但密钥来源是本地 key 文件或环境变量，不等同于 KMS；跨机器共享检查点时应使用独立密钥并考虑轮换；
- `encrypt=False` 会写出明文检查点，只应在本地调试或旧数据迁移时使用；
- 真实后端适配器没有纳入自动化测试（CI 环境没有模型服务）；
- Demo 不回退到 MockLLM；Ollama 不可用时会返回 `FAILED` 并以非零退出码结束。

---

## 安全提示

- 不要把 `.env` 提交到仓库；`.gitignore` 已忽略它。
- 只提交 `.env.example` 中的占位符。
- 如果曾经把真实 API Key 放进示例目录，请到对应平台更换/作废。

---

## 下一步建议

1. 用两个真实任务分别写脚本模式和图模式，检验脚本 API 是否真的比图 API 更顺手；
2. 补 P1 的显式暂停/人工审批恢复与执行回放；
3. 加入与独立实现基线/LangGraph 的对照实验（按论文实验章节设计）；
4. 把真实后端 smoke test 放进可选测试组。
