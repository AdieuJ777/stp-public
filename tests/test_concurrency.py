"""B-04：并发 run 的事件隔离，以及按 run_id 读取事件。"""
from __future__ import annotations

import asyncio

from stp import (
    EventType,
    FrameworkError,
    JsonCheckpointStore,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    task,
)


def _spec(task_id: str, steps: int) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        max_steps=steps + 5,
    )


def _linear_task(task_id: str, steps: int):
    spec = _spec(task_id, steps)

    @task(spec)
    def build(ctx):
        for index in range(steps):
            def fn(_data, index=index):
                return {"value": index}

            ctx.step(f"s{index}", func=fn, input={}, output_key=f"v{index}")
        ctx.end({"value": ctx.state[f"v{steps - 1}"]["value"]})

    return build


def test_concurrent_runs_do_not_share_events():
    runtime = TaskRuntime()
    short = _linear_task("TSK-SHORT", 2)
    long = _linear_task("TSK-LONG", 5)

    async def scenario():
        return await asyncio.gather(
            short.run({}, runtime=runtime),
            long.run({}, runtime=runtime),
        )

    short_run, long_run = asyncio.run(scenario())
    assert short_run.status == TaskStatus.SUCCEEDED
    assert long_run.status == TaskStatus.SUCCEEDED

    short_events = runtime.events_for(short_run.run_id)
    long_events = runtime.events_for(long_run.run_id)
    assert short_events and long_events
    assert {event.run_id for event in short_events} == {short_run.run_id}
    assert {event.run_id for event in long_events} == {long_run.run_id}
    assert len(short_events) != len(long_events)

    # 兼容层：last_events 仍只代表“最近一次”，不再是唯一访问方式。
    assert {event.run_id for event in runtime.last_events} in (
        {short_run.run_id},
        {long_run.run_id},
    )


def test_events_for_unknown_run_is_empty():
    runtime = TaskRuntime()
    assert runtime.events_for("missing-run") == []


def test_forget_run_releases_only_that_run():
    runtime = TaskRuntime()
    first = asyncio.run(_linear_task("TSK-FIRST", 2).run({}, runtime=runtime))
    second = asyncio.run(_linear_task("TSK-SECOND", 3).run({}, runtime=runtime))

    runtime.forget_run(first.run_id)
    assert runtime.events_for(first.run_id) == []
    assert runtime.events_for(second.run_id)


def test_retained_run_events_are_bounded():
    runtime = TaskRuntime(max_retained_runs=2)
    runs = [
        asyncio.run(_linear_task(f"TSK-CAP-{index}", 2).run({}, runtime=runtime))
        for index in range(3)
    ]

    assert runtime.events_for(runs[0].run_id) == []
    assert runtime.events_for(runs[1].run_id)
    assert runtime.events_for(runs[2].run_id)


def test_resume_events_are_isolated_from_original_run(tmp_path):
    calls = {"count": 0}

    def flaky(_data):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FrameworkError("temporary failure")
        return {"value": 1}

    spec = _spec("TSK-RESUME-EVENTS", 1)

    @task(spec)
    def build(ctx):
        ctx.step("flaky", func=flaky, input={}, output_key="v")
        ctx.end({"value": ctx.state["v"]["value"]})

    runtime = TaskRuntime(checkpoint_store=JsonCheckpointStore(tmp_path))
    first = asyncio.run(build.run({}, runtime=runtime))
    resumed = asyncio.run(build.resume(run_id=first.run_id, runtime=runtime))

    assert first.status == TaskStatus.FAILED
    assert resumed.status == TaskStatus.SUCCEEDED
    assert first.run_id != resumed.run_id

    first_events = runtime.events_for(first.run_id)
    resumed_events = runtime.events_for(resumed.run_id)
    assert {event.run_id for event in first_events} == {first.run_id}
    assert {event.run_id for event in resumed_events} == {resumed.run_id}
    assert any(event.event_type == EventType.TASK_FAILED for event in first_events)
    assert any(event.event_type == EventType.TASK_RESUMED for event in resumed_events)
    assert not any(event.event_type == EventType.TASK_RESUMED for event in first_events)
