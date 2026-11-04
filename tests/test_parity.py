"""双模式 parity：脚本 API 与图 API 在重叠能力上的行为等价。

覆盖 `docs/dual_mode_matrix.md` §6 的 1–5：
嵌套分支、工具失败/重试、LLM 步骤、空分支 case、取消。
"""
from __future__ import annotations

import asyncio

from stp import (
    CancellationToken,
    EventType,
    FrameworkError,
    GraphBuilder,
    GraphTask,
    JsonCheckpointStore,
    MockLLM,
    RetryPolicy,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    Tool,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    task,
)
from stp.diff import compare_runs
from stp.refs import Ref


def _spec(
    task_id: str,
    *,
    output_schema: dict | None = None,
    max_attempts: int = 1,
    max_steps: int = 20,
    max_model_calls: int = 20,
    timeout_seconds: float = 10.0,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        retry_policy=RetryPolicy(
            max_attempts=max_attempts,
            backoff_factor=1.0,
            initial_delay_seconds=0.001,
        ),
        max_steps=max_steps,
        max_model_calls=max_model_calls,
        timeout_seconds=timeout_seconds,
    )


def _assert_equivalent(left_run, left_runtime, right_run, right_runtime, fields):
    left_events = left_runtime.events_for(left_run.run_id)
    right_events = right_runtime.events_for(right_run.run_id)
    report = compare_runs(
        left_run,
        right_run,
        left_events=left_events,
        right_events=right_events,
        strict_fields=list(fields),
    )
    assert report.equivalent, report.differences
    return report


# ---------------------------------------------------------------------------
# 1. 嵌套分支
# ---------------------------------------------------------------------------
def _nested_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step(
            "classify",
            func=lambda _data: {"amount": 1500, "region": "EU"},
            input={},
            output_key="expense",
        )

        def high(c):
            def eu(c2):
                c2.step(
                    "high_eu",
                    func=lambda _data: {"result": "high-eu"},
                    input={},
                    output_key="result",
                )

            def other(c2):
                c2.step(
                    "high_other",
                    func=lambda _data: {"result": "high-other"},
                    input={},
                    output_key="result",
                )

            c.branch(
                "inner",
                condition=lambda state: (
                    "eu" if state["expense"]["region"] == "EU" else "other"
                ),
                cases={"eu": eu, "other": other},
                default="other",
            )

        def low(c):
            c.step(
                "low",
                func=lambda _data: {"result": "low"},
                input={},
                output_key="result",
            )

        ctx.branch(
            "outer",
            condition=lambda state: (
                "high" if state["expense"]["amount"] > 1000 else "low"
            ),
            cases={"high": high, "low": low},
            default="low",
        )
        ctx.end({"result": ctx.state["result"]["result"]})

    return build


def _nested_graph(spec):
    builder = GraphBuilder(spec)
    classify = builder.add_step(
        "classify",
        func=lambda _data: {"amount": 1500, "region": "EU"},
        input={},
        output_key="expense",
    )
    outer = builder.add_branch(
        "outer",
        condition=lambda state: (
            "high" if state["expense"]["amount"] > 1000 else "low"
        ),
        default="low",
    )
    inner = builder.add_branch(
        "inner",
        condition=lambda state: (
            "eu" if state["expense"]["region"] == "EU" else "other"
        ),
        default="other",
    )
    high_eu = builder.add_step(
        "high_eu",
        func=lambda _data: {"result": "high-eu"},
        input={},
        output_key="result",
    )
    high_other = builder.add_step(
        "high_other",
        func=lambda _data: {"result": "high-other"},
        input={},
        output_key="result",
    )
    low = builder.add_step(
        "low",
        func=lambda _data: {"result": "low"},
        input={},
        output_key="result",
    )
    inner_join = builder.add_join("inner_join")
    outer_join = builder.add_join("outer_join")
    end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})

    builder.set_entry(classify)
    builder.add_edge(classify, outer)
    builder.add_route(outer, "high", inner)
    builder.add_route(outer, "low", low)
    builder.add_route(inner, "eu", high_eu)
    builder.add_route(inner, "other", high_other)
    builder.add_edge(high_eu, inner_join)
    builder.add_edge(high_other, inner_join)
    builder.add_edge(inner_join, outer_join)
    builder.add_edge(low, outer_join)
    builder.add_edge(outer_join, end)
    return GraphTask(spec, builder)


def test_nested_branch_parity():
    spec = _spec("TSK-PARITY-NESTED")
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(_nested_script(spec).run({"x": 1}, runtime=script_runtime))
    graph_run = asyncio.run(_nested_graph(spec).run({"x": 1}, runtime=graph_runtime))

    assert script_run.status == TaskStatus.SUCCEEDED
    assert graph_run.status == TaskStatus.SUCCEEDED
    assert script_run.output == {"result": "high-eu"}
    assert graph_run.output == {"result": "high-eu"}
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, ["result"])


# ---------------------------------------------------------------------------
# 2. 工具失败 + 重试
# ---------------------------------------------------------------------------
class _AlwaysFailTool(Tool):
    spec = ToolSpec(
        tool_name="always_fail",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        side_effect=False,
        idempotent=True,
    )

    async def execute(self, input_data, context):
        raise ToolExecutionError("boom")


def _failure_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step("call", tool="always_fail", input={}, output_key="v")
        ctx.end({"result": ctx.state["v"]["result"]})

    return build


def _failure_graph(spec):
    builder = GraphBuilder(spec)
    call = builder.add_step("call", tool="always_fail", input={}, output_key="v")
    end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
    builder.set_entry(call)
    builder.add_edge(call, end)
    return GraphTask(spec, builder)


def test_tool_failure_and_retry_parity():
    spec = _spec("TSK-PARITY-FAIL", max_attempts=2)
    script_runtime = TaskRuntime(tools=ToolRegistry([_AlwaysFailTool()]))
    graph_runtime = TaskRuntime(tools=ToolRegistry([_AlwaysFailTool()]))

    script_run = asyncio.run(_failure_script(spec).run({"x": 1}, runtime=script_runtime))
    graph_run = asyncio.run(_failure_graph(spec).run({"x": 1}, runtime=graph_runtime))

    assert script_run.status == TaskStatus.FAILED
    assert graph_run.status == TaskStatus.FAILED
    assert script_run.error and script_run.error.code == "E_TOOL_CALL"
    assert graph_run.error and graph_run.error.code == "E_TOOL_CALL"

    report = _assert_equivalent(
        script_run, script_runtime, graph_run, graph_runtime, []
    )
    assert report.observations["left_retries"] == 1
    assert report.observations["right_retries"] == 1
    assert report.observations["left_tools"] == ["always_fail", "always_fail"]
    assert report.observations["right_tools"] == ["always_fail", "always_fail"]


# ---------------------------------------------------------------------------
# 3. LLM 步骤
# ---------------------------------------------------------------------------
def _llm_schema():
    return {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def _llm_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step("extract", llm="return a value", output_schema=_llm_schema(), output_key="data")
        ctx.end({"value": ctx.state["data"]["value"]})

    return build


def _llm_graph(spec):
    builder = GraphBuilder(spec)
    extract = builder.add_step(
        "extract",
        llm="return a value",
        output_schema=_llm_schema(),
        output_key="data",
    )
    end = builder.add_end("end", {"value": Ref("state", "data", ("value",))})
    builder.set_entry(extract)
    builder.add_edge(extract, end)
    return GraphTask(spec, builder)


def test_llm_step_parity():
    spec = _spec(
        "TSK-PARITY-LLM",
        output_schema=_llm_schema(),
    )
    script_backend = MockLLM([{"value": 7}])
    graph_backend = MockLLM([{"value": 7}])
    script_runtime = TaskRuntime(backend=script_backend)
    graph_runtime = TaskRuntime(backend=graph_backend)

    script_run = asyncio.run(_llm_script(spec).run({"x": 1}, runtime=script_runtime))
    graph_run = asyncio.run(_llm_graph(spec).run({"x": 1}, runtime=graph_runtime))

    assert script_run.status == TaskStatus.SUCCEEDED
    assert graph_run.status == TaskStatus.SUCCEEDED
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, ["value"])

    for runtime, backend, run in (
        (script_runtime, script_backend, script_run),
        (graph_runtime, graph_backend, graph_run),
    ):
        assert len(backend.calls) == 1
        llm_end = [
            event
            for event in runtime.events_for(run.run_id)
            if event.event_type == EventType.LLM_CALL_END
        ]
        assert len(llm_end) == 1


# ---------------------------------------------------------------------------
# 4. 空分支 case
# ---------------------------------------------------------------------------
def _empty_case_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step(
            "base",
            func=lambda _data: {"result": "base"},
            input={},
            output_key="base",
        )

        def empty(c):
            pass

        def work(c):
            c.step(
                "work",
                func=lambda _data: {"result": "work"},
                input={},
                output_key="unused",
            )

        ctx.branch(
            "route",
            condition=lambda state: "empty",
            cases={"empty": empty, "work": work},
            default="work",
        )
        ctx.end({"result": ctx.state["base"]["result"]})

    return build


def _empty_case_graph(spec):
    builder = GraphBuilder(spec)
    base = builder.add_step(
        "base",
        func=lambda _data: {"result": "base"},
        input={},
        output_key="base",
    )
    route = builder.add_branch(
        "route",
        condition=lambda state: "empty",
        default="work",
    )
    work = builder.add_step(
        "work",
        func=lambda _data: {"result": "work"},
        input={},
        output_key="unused",
    )
    join = builder.add_join("route_join")
    end = builder.add_end("end", {"result": Ref("state", "base", ("result",))})

    builder.set_entry(base)
    builder.add_edge(base, route)
    builder.add_route(route, "empty", join)
    builder.add_route(route, "work", work)
    builder.add_edge(work, join)
    builder.add_edge(join, end)
    return GraphTask(spec, builder)


def test_empty_branch_case_parity():
    spec = _spec("TSK-PARITY-EMPTY")
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(
        _empty_case_script(spec).run({"x": 1}, runtime=script_runtime)
    )
    graph_run = asyncio.run(_empty_case_graph(spec).run({"x": 1}, runtime=graph_runtime))

    assert script_run.status == TaskStatus.SUCCEEDED
    assert graph_run.status == TaskStatus.SUCCEEDED
    assert script_run.output == {"result": "base"}
    assert graph_run.output == {"result": "base"}
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, ["result"])

    for runtime, run in ((script_runtime, script_run), (graph_runtime, graph_run)):
        started = [
            event.payload.get("name")
            for event in runtime.events_for(run.run_id)
            if event.event_type == EventType.STEP_START
        ]
        assert "work" not in started


# ---------------------------------------------------------------------------
# 5. 取消
# ---------------------------------------------------------------------------
def _cancel_script(spec, token, calls):
    @task(spec)
    def build(ctx):
        def first(_data):
            calls["first"] += 1
            token.cancel()
            return {"result": "first"}

        def second(_data):
            calls["second"] += 1
            return {"result": "second"}

        ctx.step("first", func=first, input={}, output_key="first")
        ctx.step("second", func=second, input={}, output_key="second")
        ctx.end({"result": ctx.state["second"]["result"]})

    return build


def _cancel_graph(spec, token, calls):
    builder = GraphBuilder(spec)

    def first(_data):
        calls["first"] += 1
        token.cancel()
        return {"result": "first"}

    def second(_data):
        calls["second"] += 1
        return {"result": "second"}

    first_node = builder.add_step("first", func=first, input={}, output_key="first")
    second_node = builder.add_step("second", func=second, input={}, output_key="second")
    end = builder.add_end("end", {"result": Ref("state", "second", ("result",))})
    builder.set_entry(first_node)
    builder.add_edge(first_node, second_node)
    builder.add_edge(second_node, end)
    return GraphTask(spec, builder)


def test_cancellation_parity():
    spec = _spec("TSK-PARITY-CANCEL")
    script_token = CancellationToken()
    graph_token = CancellationToken()
    script_calls = {"first": 0, "second": 0}
    graph_calls = {"first": 0, "second": 0}
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(
        _cancel_script(spec, script_token, script_calls).run(
            {"x": 1}, runtime=script_runtime, cancellation=script_token
        )
    )
    graph_run = asyncio.run(
        _cancel_graph(spec, graph_token, graph_calls).run(
            {"x": 1}, runtime=graph_runtime, cancellation=graph_token
        )
    )

    assert script_run.status == TaskStatus.CANCELLED
    assert graph_run.status == TaskStatus.CANCELLED
    assert script_run.error and script_run.error.code == "E_CANCELLED"
    assert graph_run.error and graph_run.error.code == "E_CANCELLED"
    assert script_calls == {"first": 1, "second": 0}
    assert graph_calls == {"first": 1, "second": 0}
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, [])

    for runtime, run in ((script_runtime, script_run), (graph_runtime, graph_run)):
        events = runtime.events_for(run.run_id)
        assert any(event.event_type == EventType.TASK_CANCELLED for event in events)
        started = [
            event.payload.get("name")
            for event in events
            if event.event_type == EventType.STEP_START
        ]
        assert started == ["first"]


# ---------------------------------------------------------------------------
# 6. 分支恢复 parity（FR-029）
# ---------------------------------------------------------------------------
def _resume_spec(task_id: str) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
        max_steps=20,
        timeout_seconds=10.0,
    )


def _branch_resume_script(spec, calls, route_holder):
    def classify(_data):
        calls["classify"] += 1
        return {"amount": 1200}

    def high(_data):
        calls["high"] += 1
        if calls["high"] == 1:
            raise FrameworkError("temporary failure")
        return {"status": "approved"}

    def low(_data):
        calls["low"] += 1
        return {"status": "recorded"}

    @task(spec)
    def build(ctx):
        ctx.step("classify", func=classify, input={}, output_key="expense")

        def high_case(c):
            c.step("high", func=high, input={}, output_key="result")

        def low_case(c):
            c.step("low", func=low, input={}, output_key="result")

        ctx.branch(
            "route",
            condition=lambda state: route_holder["route"],
            cases={"high": high_case, "low": low_case},
            default="low",
        )
        ctx.end({"status": ctx.state["result"]["status"]})

    return build


def _branch_resume_graph(spec, calls, route_holder):
    def classify(_data):
        calls["classify"] += 1
        return {"amount": 1200}

    def high(_data):
        calls["high"] += 1
        if calls["high"] == 1:
            raise FrameworkError("temporary failure")
        return {"status": "approved"}

    def low(_data):
        calls["low"] += 1
        return {"status": "recorded"}

    builder = GraphBuilder(spec)
    classify_node = builder.add_step(
        "classify", func=classify, input={}, output_key="expense"
    )
    route = builder.add_branch(
        "route",
        condition=lambda state: route_holder["route"],
        default="low",
    )
    high_node = builder.add_step("high", func=high, input={}, output_key="result")
    low_node = builder.add_step("low", func=low, input={}, output_key="result")
    join = builder.add_join("join")
    end = builder.add_end("end", {"status": Ref("state", "result", ("status",))})

    builder.set_entry(classify_node)
    builder.add_edge(classify_node, route)
    builder.add_route(route, "high", high_node)
    builder.add_route(route, "low", low_node)
    builder.add_edge(high_node, join)
    builder.add_edge(low_node, join)
    builder.add_edge(join, end)
    return GraphTask(spec, builder)


def test_branch_resume_parity(tmp_path):
    spec = _resume_spec("TSK-PARITY-RESUME-BRANCH")
    script_calls = {"classify": 0, "high": 0, "low": 0}
    graph_calls = {"classify": 0, "high": 0, "low": 0}
    script_route = {"route": "high"}
    graph_route = {"route": "high"}
    script_task = _branch_resume_script(spec, script_calls, script_route)
    graph_task = _branch_resume_graph(spec, graph_calls, graph_route)
    script_runtime = TaskRuntime(
        checkpoint_store=JsonCheckpointStore(tmp_path / "script")
    )
    graph_runtime = TaskRuntime(
        checkpoint_store=JsonCheckpointStore(tmp_path / "graph")
    )

    script_first = asyncio.run(script_task.run({}, runtime=script_runtime))
    graph_first = asyncio.run(graph_task.run({}, runtime=graph_runtime))
    assert script_first.status == TaskStatus.FAILED
    assert graph_first.status == TaskStatus.FAILED
    assert script_first.error and script_first.error.code == "E_FRAMEWORK"
    assert graph_first.error and graph_first.error.code == "E_FRAMEWORK"

    script_resumed = asyncio.run(
        script_task.resume(run_id=script_first.run_id, runtime=script_runtime)
    )
    graph_resumed = asyncio.run(
        graph_task.resume(run_id=graph_first.run_id, runtime=graph_runtime)
    )
    assert script_resumed.status == TaskStatus.SUCCEEDED
    assert graph_resumed.status == TaskStatus.SUCCEEDED
    assert script_resumed.output == {"status": "approved"}
    assert graph_resumed.output == {"status": "approved"}
    assert script_calls == {"classify": 1, "high": 2, "low": 0}
    assert graph_calls == {"classify": 1, "high": 2, "low": 0}

    _assert_equivalent(
        script_resumed, script_runtime, graph_resumed, graph_runtime, ["status"]
    )

    for runtime, run in (
        (script_runtime, script_resumed),
        (graph_runtime, graph_resumed),
    ):
        events = runtime.events_for(run.run_id)
        skipped = {
            event.payload.get("name")
            for event in events
            if event.event_type == EventType.STEP_SKIPPED
        }
        assert {"classify", "route"} <= skipped
        assert any(event.event_type == EventType.CHECKPOINT_LOADED for event in events)
        assert any(event.event_type == EventType.TASK_RESUMED for event in events)


def test_graph_branch_resume_route_mismatch(tmp_path):
    spec = _resume_spec("TSK-PARITY-RESUME-MISMATCH")
    calls = {"classify": 0, "high": 0, "low": 0}
    route_holder = {"route": "high"}
    task_obj = _branch_resume_graph(spec, calls, route_holder)
    runtime = TaskRuntime(checkpoint_store=JsonCheckpointStore(tmp_path))

    first = asyncio.run(task_obj.run({}, runtime=runtime))
    assert first.status == TaskStatus.FAILED

    route_holder["route"] = "low"
    mismatched = asyncio.run(task_obj.resume(run_id=first.run_id, runtime=runtime))
    assert mismatched.status == TaskStatus.FAILED
    assert mismatched.error is not None
    assert mismatched.error.code == "E_RESUME_UNSUPPORTED"
    assert calls == {"classify": 1, "high": 1, "low": 0}
