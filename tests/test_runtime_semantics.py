import asyncio
import time

from stp import (
    EventType,
    FunctionTool,
    TaskRuntime,
    TaskSpec,
    TaskStatus,
    TerminationCondition,
    Tool,
    ToolRegistry,
    ToolSpec,
    task,
)
from stp.models import ValidatorRef


class _SlowTool(Tool):
    spec = ToolSpec(
        tool_name="slow_tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        timeout_seconds=5.0,
    )

    async def execute(self, input_data, context):
        await asyncio.sleep(0.2)
        return {"ok": True}


def test_task_timeout_marks_inflight_tool_unknown():
    spec = TaskSpec(
        task_id="TSK-UNKNOWN",
        name="unknown",
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        timeout_seconds=0.05,
    )

    @task(spec)
    def build(ctx):
        ctx.step("slow", tool="slow_tool", input={}, output_key="v")
        ctx.end({"ok": ctx.state["v"]["ok"]})

    runtime = TaskRuntime(tools=ToolRegistry([_SlowTool()]))
    run = asyncio.run(build.run({}, runtime=runtime))
    assert run.status == TaskStatus.FAILED
    assert run.error is not None and run.error.code == "E_TASK_TIMEOUT"
    ends = [e for e in runtime.last_events if e.event_type == EventType.TOOL_CALL_END]
    assert ends
    assert ends[-1].payload["status"] == "UNKNOWN"
    assert ends[-1].payload["error_code"] == "E_TASK_TIMEOUT"


def test_business_validator_failure():
    def reject(output):
        raise ValueError("budget rule violated")

    spec = TaskSpec(
        task_id="TSK-VALIDATOR",
        name="validator",
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        validators=[ValidatorRef(name="budget", func=reject)],
    )

    @task(spec)
    def build(ctx):
        ctx.step("ok", func=lambda data: {"ok": True}, input={}, output_key="v")
        ctx.end({"ok": ctx.state["v"]["ok"]})

    run = asyncio.run(build.run({}, runtime=TaskRuntime()))
    assert run.status == TaskStatus.FAILED
    assert run.error is not None and run.error.code == "E_BUSINESS_RULE"


def test_output_match_termination_condition():
    spec = TaskSpec(
        task_id="TSK-TERM",
        name="termination",
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
        termination_condition=TerminationCondition(type="output_match", value="ok"),
    )

    @task(spec)
    def build(ctx):
        ctx.step("ok", func=lambda data: {"status": "ok"}, input={}, output_key="v")
        ctx.end({"status": ctx.state["v"]["status"]})

    run = asyncio.run(build.run({}, runtime=TaskRuntime()))
    assert run.status == TaskStatus.SUCCEEDED


def test_output_match_termination_failure():
    spec = TaskSpec(
        task_id="TSK-TERM2",
        name="termination2",
        input_schema={"type": "object"},
        output_schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
        termination_condition=TerminationCondition(type="output_match", value="missing"),
    )

    @task(spec)
    def build(ctx):
        ctx.step("ok", func=lambda data: {"status": "ok"}, input={}, output_key="v")
        ctx.end({"status": ctx.state["v"]["status"]})

    run = asyncio.run(build.run({}, runtime=TaskRuntime()))
    assert run.status == TaskStatus.FAILED
    assert run.error is not None and run.error.code == "E_TERMINATION"


# ---------------------------------------------------------------------------
# 同步 callable 不阻塞事件循环（B-02）
# ---------------------------------------------------------------------------
def _sync_io_spec(task_id: str):
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )


async def _run_with_heartbeat(task_obj, runtime):
    task = asyncio.create_task(task_obj.run({}, runtime=runtime))
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.01)
        ticks += 1
    return await task, ticks


def test_sync_function_does_not_block_event_loop():
    spec = _sync_io_spec("TSK-SYNC-FN")

    def slow(_data):
        time.sleep(0.2)
        return {"value": 1}

    @task(spec)
    def build(ctx):
        ctx.step("slow", func=slow, input={}, output_key="v")
        ctx.end({"value": ctx.state["v"]["value"]})

    run, ticks = asyncio.run(_run_with_heartbeat(build, TaskRuntime()))
    assert run.status == TaskStatus.SUCCEEDED
    assert ticks >= 5, ticks


def test_sync_function_step_timeout_is_enforced():
    spec = _sync_io_spec("TSK-SYNC-TIMEOUT")

    def slow(_data):
        time.sleep(0.25)
        return {"value": 1}

    @task(spec)
    def build(ctx):
        ctx.step("slow", func=slow, input={}, output_key="v", timeout_seconds=0.05)
        ctx.end({"value": ctx.state["v"]["value"]})

    run = asyncio.run(build.run({}, runtime=TaskRuntime()))
    assert run.status == TaskStatus.FAILED
    assert run.error is not None and run.error.code == "E_STEP_TIMEOUT"


def test_function_tool_sync_does_not_block_event_loop():
    spec = _sync_io_spec("TSK-SYNC-TOOL")

    def slow(_data):
        time.sleep(0.2)
        return {"value": 1}

    tool = FunctionTool(
        ToolSpec(
            tool_name="slow_sync",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            timeout_seconds=2.0,
        ),
        slow,
    )

    @task(spec)
    def build(ctx):
        ctx.step("slow", tool="slow_sync", input={}, output_key="v")
        ctx.end({"value": ctx.state["v"]["value"]})

    run, ticks = asyncio.run(
        _run_with_heartbeat(build, TaskRuntime(tools=ToolRegistry([tool])))
    )
    assert run.status == TaskStatus.SUCCEEDED
    assert ticks >= 5, ticks
