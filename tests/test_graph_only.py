"""图模式独有能力：多个 end、共享节点/菱形拓扑。

这些不是脚本/图 parity 用例，而是图模式表达力的回归测试；
脚本 API 有意不提供这些拓扑。
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
)
from stp.refs import Ref


def _spec(task_id: str) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "route": {"type": "string"},
            },
            "required": ["x", "route"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        max_steps=10,
        timeout_seconds=10.0,
    )


def _multi_end_task(route: str):
    spec = _spec("TSK-GRAPH-MULTI-END")
    builder = GraphBuilder(spec)
    classify = builder.add_step(
        "classify",
        func=lambda data: {"route": data["route"]},
        input={"route": Ref("input", "route")},
        output_key="classification",
    )
    branch = builder.add_branch(
        "route",
        condition=lambda state: state["classification"]["route"],
        default="a",
    )
    step_a = builder.add_step(
        "a",
        func=lambda _data: {"result": "a"},
        input={},
        output_key="a_out",
    )
    step_b = builder.add_step(
        "b",
        func=lambda _data: {"result": "b"},
        input={},
        output_key="b_out",
    )
    end_a = builder.add_end("end_a", {"result": Ref("state", "a_out", ("result",))})
    end_b = builder.add_end("end_b", {"result": Ref("state", "b_out", ("result",))})

    builder.set_entry(classify)
    builder.add_edge(classify, branch)
    builder.add_route(branch, "a", step_a)
    builder.add_route(branch, "b", step_b)
    builder.add_edge(step_a, end_a)
    builder.add_edge(step_b, end_b)
    return GraphTask(spec, builder)


def test_graph_multiple_ends_selects_active_end():
    for route, expected in (("a", "a"), ("b", "b")):
        task_obj = _multi_end_task(route)
        runtime = TaskRuntime()
        run = asyncio.run(
            task_obj.run({"x": 1, "route": route}, runtime=runtime)
        )
        assert run.status == TaskStatus.SUCCEEDED
        assert run.output == {"result": expected}


def test_graph_shared_node_diamond_runs_once():
    calls = {"shared": 0}
    spec = _spec("TSK-GRAPH-DIAMOND")
    builder = GraphBuilder(spec)
    start = builder.add_step(
        "start",
        func=lambda data: {"route": data["route"]},
        input={"route": Ref("input", "route")},
        output_key="started",
    )
    branch = builder.add_branch(
        "route",
        condition=lambda state: state["started"]["route"],
        default="left",
    )

    def shared_func(_data):
        calls["shared"] += 1
        return {"result": "shared"}

    shared = builder.add_step(
        "shared",
        func=shared_func,
        input={},
        output_key="shared_out",
    )
    end = builder.add_end("end", {"result": Ref("state", "shared_out", ("result",))})

    builder.set_entry(start)
    builder.add_edge(start, branch)
    builder.add_route(branch, "left", shared)
    builder.add_route(branch, "right", shared)
    builder.add_edge(shared, end)

    for route in ("left", "right"):
        calls["shared"] = 0
        runtime = TaskRuntime()
        run = asyncio.run(
            GraphTask(spec, builder).run({"x": 1, "route": route}, runtime=runtime)
        )
        assert run.status == TaskStatus.SUCCEEDED
        assert run.output == {"result": "shared"}
        assert calls["shared"] == 1


def test_graph_multiple_ends_resume(tmp_path):
    calls = {"classify": 0, "a": 0, "b": 0}
    spec = _spec("TSK-GRAPH-MULTI-END-RESUME")
    builder = GraphBuilder(spec)

    def classify(data):
        calls["classify"] += 1
        return {"route": data["route"]}

    def step_a(_data):
        calls["a"] += 1
        if calls["a"] == 1:
            raise FrameworkError("temporary failure")
        return {"result": "a"}

    def step_b(_data):
        calls["b"] += 1
        return {"result": "b"}

    classify_node = builder.add_step(
        "classify",
        func=classify,
        input={"route": Ref("input", "route")},
        output_key="classification",
    )
    branch = builder.add_branch(
        "route",
        condition=lambda state: state["classification"]["route"],
        default="a",
    )
    a_node = builder.add_step("a", func=step_a, input={}, output_key="a_out")
    b_node = builder.add_step("b", func=step_b, input={}, output_key="b_out")
    end_a = builder.add_end("end_a", {"result": Ref("state", "a_out", ("result",))})
    end_b = builder.add_end("end_b", {"result": Ref("state", "b_out", ("result",))})

    builder.set_entry(classify_node)
    builder.add_edge(classify_node, branch)
    builder.add_route(branch, "a", a_node)
    builder.add_route(branch, "b", b_node)
    builder.add_edge(a_node, end_a)
    builder.add_edge(b_node, end_b)

    runtime = TaskRuntime(checkpoint_store=JsonCheckpointStore(tmp_path))
    task_obj = GraphTask(spec, builder)
    first = asyncio.run(task_obj.run({"x": 1, "route": "a"}, runtime=runtime))
    assert first.status == TaskStatus.FAILED

    resumed = asyncio.run(task_obj.resume(run_id=first.run_id, runtime=runtime))
    assert resumed.status == TaskStatus.SUCCEEDED
    assert resumed.output == {"result": "a"}
    assert calls == {"classify": 1, "a": 2, "b": 0}

    events = runtime.events_for(resumed.run_id)
    skipped = {
        event.payload.get("name")
        for event in events
        if event.event_type == EventType.STEP_SKIPPED
    }
    started = [
        event.payload.get("name")
        for event in events
        if event.event_type == EventType.STEP_START
    ]
    assert {"classify", "route"} <= skipped
    assert started == ["a"]
