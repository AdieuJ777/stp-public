"""双模式 parity：步骤/任务超时与执行限制。

覆盖 `docs/dual_mode_matrix.md` §6 剩余项中的：
- 步骤超时；
- 任务超时；
- 模型调用次数限制；
- 步数限制（含当前静态/运行时口径差异）。
"""
from __future__ import annotations

import asyncio

from stp import (
    EventType,
    GraphBuilder,
    GraphTask,
    MockLLM,
    RetryPolicy,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    task,
)
from stp.diff import compare_runs
from stp.refs import Ref


def _spec(
    task_id: str,
    *,
    output_schema: dict | None = None,
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
            max_attempts=1,
            backoff_factor=1.0,
            initial_delay_seconds=0.001,
        ),
        max_steps=max_steps,
        max_model_calls=max_model_calls,
        timeout_seconds=timeout_seconds,
    )


def _llm_schema() -> dict:
    return {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def _assert_equivalent(left_run, left_runtime, right_run, right_runtime, fields):
    report = compare_runs(
        left_run,
        right_run,
        left_events=left_runtime.events_for(left_run.run_id),
        right_events=right_runtime.events_for(right_run.run_id),
        strict_fields=list(fields),
    )
    assert report.equivalent, report.differences


async def _slow_async(_data):
    await asyncio.sleep(0.2)
    return {"result": "late"}


# ---------------------------------------------------------------------------
# 步骤超时
# ---------------------------------------------------------------------------
def _step_timeout_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step(
            "slow",
            func=_slow_async,
            input={},
            output_key="v",
            timeout_seconds=0.05,
        )
        ctx.end({"result": ctx.state["v"]["result"]})

    return build


def _step_timeout_graph(spec):
    builder = GraphBuilder(spec)
    slow = builder.add_step(
        "slow",
        func=_slow_async,
        input={},
        output_key="v",
        timeout_seconds=0.05,
    )
    end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
    builder.set_entry(slow)
    builder.add_edge(slow, end)
    return GraphTask(spec, builder)


def test_step_timeout_parity():
    spec = _spec("TSK-PARITY-STEP-TIMEOUT")
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(
        _step_timeout_script(spec).run({"x": 1}, runtime=script_runtime)
    )
    graph_run = asyncio.run(
        _step_timeout_graph(spec).run({"x": 1}, runtime=graph_runtime)
    )

    assert script_run.status == TaskStatus.FAILED
    assert graph_run.status == TaskStatus.FAILED
    assert script_run.error and script_run.error.code == "E_STEP_TIMEOUT"
    assert graph_run.error and graph_run.error.code == "E_STEP_TIMEOUT"
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, [])

    for runtime, run in (
        (script_runtime, script_run),
        (graph_runtime, graph_run),
    ):
        codes = [
            event.payload.get("error_code")
            for event in runtime.events_for(run.run_id)
            if event.event_type == EventType.ERROR_OCCURRED
        ]
        assert "E_STEP_TIMEOUT" in codes


# ---------------------------------------------------------------------------
# 任务超时
# ---------------------------------------------------------------------------
def _task_timeout_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step("slow", func=_slow_async, input={}, output_key="v")
        ctx.end({"result": ctx.state["v"]["result"]})

    return build


def _task_timeout_graph(spec):
    builder = GraphBuilder(spec)
    slow = builder.add_step("slow", func=_slow_async, input={}, output_key="v")
    end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
    builder.set_entry(slow)
    builder.add_edge(slow, end)
    return GraphTask(spec, builder)


def test_task_timeout_parity():
    spec = _spec("TSK-PARITY-TASK-TIMEOUT", timeout_seconds=0.05)
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(
        _task_timeout_script(spec).run({"x": 1}, runtime=script_runtime)
    )
    graph_run = asyncio.run(
        _task_timeout_graph(spec).run({"x": 1}, runtime=graph_runtime)
    )

    assert script_run.status == TaskStatus.FAILED
    assert graph_run.status == TaskStatus.FAILED
    assert script_run.error and script_run.error.code == "E_TASK_TIMEOUT"
    assert graph_run.error and graph_run.error.code == "E_TASK_TIMEOUT"
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, [])


# ---------------------------------------------------------------------------
# 模型调用次数限制
# ---------------------------------------------------------------------------
def _two_llm_script(spec):
    @task(spec)
    def build(ctx):
        ctx.step("llm1", llm="first", output_schema=_llm_schema(), output_key="a")
        ctx.step("llm2", llm="second", output_schema=_llm_schema(), output_key="b")
        ctx.end({"value": ctx.state["b"]["value"]})

    return build


def _two_llm_graph(spec):
    builder = GraphBuilder(spec)
    first = builder.add_step(
        "llm1", llm="first", output_schema=_llm_schema(), output_key="a"
    )
    second = builder.add_step(
        "llm2", llm="second", output_schema=_llm_schema(), output_key="b"
    )
    end = builder.add_end("end", {"value": Ref("state", "b", ("value",))})
    builder.set_entry(first)
    builder.add_edge(first, second)
    builder.add_edge(second, end)
    return GraphTask(spec, builder)


def test_model_call_limit_parity():
    spec = _spec(
        "TSK-PARITY-MODEL-LIMIT",
        output_schema=_llm_schema(),
        max_model_calls=1,
    )
    script_backend = MockLLM([{"value": 1}, {"value": 2}])
    graph_backend = MockLLM([{"value": 1}, {"value": 2}])
    script_runtime = TaskRuntime(backend=script_backend)
    graph_runtime = TaskRuntime(backend=graph_backend)

    script_run = asyncio.run(
        _two_llm_script(spec).run({"x": 1}, runtime=script_runtime)
    )
    graph_run = asyncio.run(
        _two_llm_graph(spec).run({"x": 1}, runtime=graph_runtime)
    )

    assert script_run.status == TaskStatus.FAILED
    assert graph_run.status == TaskStatus.FAILED
    assert script_run.error and script_run.error.code == "E_LIMIT_EXCEEDED"
    assert graph_run.error and graph_run.error.code == "E_LIMIT_EXCEEDED"
    assert len(script_backend.calls) == 1
    assert len(graph_backend.calls) == 1
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, [])


# ---------------------------------------------------------------------------
# 步数限制
# ---------------------------------------------------------------------------
def _max_steps_script(spec, calls):
    def base(_data):
        calls["base"] += 1
        return {"result": "base"}

    def high(_data):
        calls["high"] += 1
        return {"result": "high"}

    def low(_data):
        calls["low"] += 1
        return {"result": "low"}

    @task(spec)
    def build(ctx):
        ctx.step("base", func=base, input={}, output_key="base")

        def high_case(c):
            c.step("high", func=high, input={}, output_key="result")

        def low_case(c):
            c.step("low", func=low, input={}, output_key="result")

        ctx.branch(
            "route",
            condition=lambda state: "high",
            cases={"high": high_case, "low": low_case},
            default="low",
        )
        ctx.end({"result": ctx.state["result"]["result"]})

    return build


def _max_steps_graph(spec, calls):
    def base(_data):
        calls["base"] += 1
        return {"result": "base"}

    def high(_data):
        calls["high"] += 1
        return {"result": "high"}

    def low(_data):
        calls["low"] += 1
        return {"result": "low"}

    builder = GraphBuilder(spec)
    base_node = builder.add_step("base", func=base, input={}, output_key="base")
    route = builder.add_branch("route", condition=lambda state: "high", default="low")
    high_node = builder.add_step("high", func=high, input={}, output_key="result")
    low_node = builder.add_step("low", func=low, input={}, output_key="result")
    join = builder.add_join("join")
    end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})

    builder.set_entry(base_node)
    builder.add_edge(base_node, route)
    builder.add_route(route, "high", high_node)
    builder.add_route(route, "low", low_node)
    builder.add_edge(high_node, join)
    builder.add_edge(low_node, join)
    builder.add_edge(join, end)
    return GraphTask(spec, builder)


def test_max_steps_limit_parity():
    # 静态校验只数 step 节点（base + high = 2），运行时 branch 也计数，
    # 因此 max_steps=2 会通过静态校验、在 branch 后的 high 步骤触发限制。
    # 这是当前 max_steps 口径的已知差异（backlog B-06），两种模式行为一致。
    spec = _spec("TSK-PARITY-MAX-STEPS", max_steps=2)
    script_calls = {"base": 0, "high": 0, "low": 0}
    graph_calls = {"base": 0, "high": 0, "low": 0}
    script_runtime = TaskRuntime()
    graph_runtime = TaskRuntime()

    script_run = asyncio.run(
        _max_steps_script(spec, script_calls).run({"x": 1}, runtime=script_runtime)
    )
    graph_run = asyncio.run(
        _max_steps_graph(spec, graph_calls).run({"x": 1}, runtime=graph_runtime)
    )

    assert script_run.status == TaskStatus.FAILED
    assert graph_run.status == TaskStatus.FAILED
    assert script_run.error and script_run.error.code == "E_LIMIT_EXCEEDED"
    assert graph_run.error and graph_run.error.code == "E_LIMIT_EXCEEDED"
    assert script_calls == {"base": 1, "high": 0, "low": 0}
    assert graph_calls == {"base": 1, "high": 0, "low": 0}
    _assert_equivalent(script_run, script_runtime, graph_run, graph_runtime, [])
