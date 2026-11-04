"""双模式 parity：嵌套分支的恢复行为。

覆盖 `docs/dual_mode_matrix.md` §6 剩余项中的“嵌套分支恢复”：
外层 branch → 内层 branch → 失败 → 恢复；比较脚本/图是否跳过三层已完成节点。
"""
from __future__ import annotations

import asyncio

from stp import (
    EventType,
    FrameworkError,
    GraphBuilder,
    GraphTask,
    JsonCheckpointStore,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    task,
)
from stp.diff import compare_runs
from stp.refs import Ref


def _spec(task_id: str) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        max_steps=20,
        timeout_seconds=10.0,
    )


def _make_funcs(calls, *, fail_first_eu: bool):
    def classify(_data):
        calls["classify"] += 1
        return {"amount": 1500, "region": "EU"}

    def high_eu(_data):
        calls["high_eu"] += 1
        if fail_first_eu and calls["high_eu"] == 1:
            raise FrameworkError("inner branch failed")
        return {"result": "high-eu"}

    def high_other(_data):
        calls["high_other"] += 1
        return {"result": "high-other"}

    def low(_data):
        calls["low"] += 1
        return {"result": "low"}

    return classify, high_eu, high_other, low


def _nested_script(spec, calls, outer_route, inner_route):
    classify, high_eu, high_other, low = _make_funcs(calls, fail_first_eu=True)

    @task(spec)
    def build(ctx):
        ctx.step("classify", func=classify, input={}, output_key="expense")

        def high(c):
            def eu(c2):
                c2.step("high_eu", func=high_eu, input={}, output_key="result")

            def other(c2):
                c2.step("high_other", func=high_other, input={}, output_key="result")

            c.branch(
                "inner",
                condition=lambda state: inner_route["route"],
                cases={"eu": eu, "other": other},
                default="other",
            )

        def low_case(c):
            c.step("low", func=low, input={}, output_key="result")

        ctx.branch(
            "outer",
            condition=lambda state: outer_route["route"],
            cases={"high": high, "low": low_case},
            default="low",
        )
        ctx.end({"result": ctx.state["result"]["result"]})

    return build


def _nested_graph(spec, calls, outer_route, inner_route):
    classify, high_eu, high_other, low = _make_funcs(calls, fail_first_eu=True)

    builder = GraphBuilder(spec)
    classify_node = builder.add_step(
        "classify", func=classify, input={}, output_key="expense"
    )
    outer = builder.add_branch(
        "outer",
        condition=lambda state: outer_route["route"],
        default="low",
    )
    inner = builder.add_branch(
        "inner",
        condition=lambda state: inner_route["route"],
        default="other",
    )
    eu_node = builder.add_step("high_eu", func=high_eu, input={}, output_key="result")
    other_node = builder.add_step(
        "high_other", func=high_other, input={}, output_key="result"
    )
    low_node = builder.add_step("low", func=low, input={}, output_key="result")
    inner_join = builder.add_join("inner_join")
    outer_join = builder.add_join("outer_join")
    end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})

    builder.set_entry(classify_node)
    builder.add_edge(classify_node, outer)
    builder.add_route(outer, "high", inner)
    builder.add_route(outer, "low", low_node)
    builder.add_route(inner, "eu", eu_node)
    builder.add_route(inner, "other", other_node)
    builder.add_edge(eu_node, inner_join)
    builder.add_edge(other_node, inner_join)
    builder.add_edge(inner_join, outer_join)
    builder.add_edge(low_node, outer_join)
    builder.add_edge(outer_join, end)
    return GraphTask(spec, builder)


def test_nested_branch_resume_parity(tmp_path):
    spec = _spec("TSK-PARITY-NESTED-RESUME")
    script_calls = {"classify": 0, "high_eu": 0, "high_other": 0, "low": 0}
    graph_calls = {"classify": 0, "high_eu": 0, "high_other": 0, "low": 0}
    script_outer, script_inner = {"route": "high"}, {"route": "eu"}
    graph_outer, graph_inner = {"route": "high"}, {"route": "eu"}
    script_task = _nested_script(spec, script_calls, script_outer, script_inner)
    graph_task = _nested_graph(spec, graph_calls, graph_outer, graph_inner)
    script_runtime = TaskRuntime(
        checkpoint_store=JsonCheckpointStore(tmp_path / "script")
    )
    graph_runtime = TaskRuntime(
        checkpoint_store=JsonCheckpointStore(tmp_path / "graph")
    )

    script_first = asyncio.run(script_task.run({"x": 1}, runtime=script_runtime))
    graph_first = asyncio.run(graph_task.run({"x": 1}, runtime=graph_runtime))
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
    assert script_resumed.output == {"result": "high-eu"}
    assert graph_resumed.output == {"result": "high-eu"}
    assert script_calls == {"classify": 1, "high_eu": 2, "high_other": 0, "low": 0}
    assert graph_calls == {"classify": 1, "high_eu": 2, "high_other": 0, "low": 0}

    report = compare_runs(
        script_resumed,
        graph_resumed,
        left_events=script_runtime.events_for(script_resumed.run_id),
        right_events=graph_runtime.events_for(graph_resumed.run_id),
        strict_fields=["result"],
    )
    assert report.equivalent, report.differences

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
        assert {"classify", "outer", "inner"} <= skipped
        started = {
            event.payload.get("name")
            for event in events
            if event.event_type == EventType.STEP_START
        }
        assert started == {"high_eu"}
        assert any(event.event_type == EventType.CHECKPOINT_LOADED for event in events)
        assert any(event.event_type == EventType.TASK_RESUMED for event in events)
