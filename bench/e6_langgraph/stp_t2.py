"""E6 T2：STP 失败 + 恢复实现。"""

from __future__ import annotations

import asyncio
import tempfile

from stp import JsonCheckpointStore, RetryPolicy, TaskRuntime, TaskSpec, task

from shared import step_fn

SPEC = TaskSpec(
    task_id="TSK-E6-T2",
    name="e6_t2",
    input_schema={
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    },
    retry_policy=RetryPolicy(
        max_attempts=1,
        backoff_factor=1.0,
        initial_delay_seconds=0.001,
    ),
    max_steps=10,
    timeout_seconds=10.0,
)


def build(calls: dict[str, int]):
    @task(SPEC)
    def task_build(ctx):
        for index in range(1, 6):
            ctx.step(
                f"s{index}",
                func=step_fn(index, calls),
                input={},
                output_key=f"s{index}",
            )
        ctx.end({"value": ctx.state["s5"]["value"]})

    return task_build


def run() -> dict:
    calls: dict[str, int] = {}
    task_obj = build(calls)
    with tempfile.TemporaryDirectory(prefix="e6-stp-t2-") as temp:
        runtime = TaskRuntime(checkpoint_store=JsonCheckpointStore(temp))
        first = asyncio.run(task_obj.run({"x": 1}, runtime=runtime))
        resumed = asyncio.run(task_obj.resume(run_id=first.run_id, runtime=runtime))
    return {
        "first_status": first.status.value,
        "resume_status": resumed.status.value,
        "output": resumed.output,
        "calls": calls,
    }


if __name__ == "__main__":
    print(run())
