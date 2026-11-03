"""P1 最小基准：检查点开销、文件大小与恢复耗时。

这是确定性、离线、无模型依赖的测量脚本，用于 NFR-007 的最小证据，
不预设性能阈值。运行：

    python bench/p1_benchmark.py
"""
from __future__ import annotations

import asyncio
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from stp import (
    FrameworkError,
    JsonCheckpointStore,
    TaskRuntime,
    TaskSpec,
    task,
)

REPEATS = 15
STEP_COUNTS = (5, 10, 20)


def make_spec(steps: int) -> TaskSpec:
    return TaskSpec(
        task_id=f"TSK-BENCH-{steps}",
        name=f"bench_{steps}",
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
        max_steps=steps + 5,
        timeout_seconds=30.0,
    )


def build_task(
    steps: int,
    *,
    calls: dict[str, int] | None = None,
    fail_at: int | None = None,
):
    spec = make_spec(steps)

    @task(spec)
    def build(ctx):
        for index in range(steps):
            name = f"s{index}"

            def fn(_data, idx=index, step_name=name):
                if calls is not None:
                    calls[step_name] = calls.get(step_name, 0) + 1
                if fail_at is not None and idx == fail_at and calls[step_name] == 1:
                    raise FrameworkError("benchmark injected failure")
                return {"value": idx}

            ctx.step(name, func=fn, input={}, output_key=name)
        ctx.end({"value": ctx.state[f"s{steps - 1}"]["value"]})

    return build


async def timed_run(task_obj, store=None) -> tuple[float, Any]:
    runtime = TaskRuntime(checkpoint_store=store)
    started = time.perf_counter()
    run = await task_obj.run({"x": 1}, runtime=runtime)
    return (time.perf_counter() - started) * 1000, run


def checkpoint_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.glob("**/*.json"))


async def main() -> None:
    print("## P1 checkpoint benchmark\n")
    print("| steps | no checkpoint median ms | checkpoint median ms | overhead ms | checkpoint bytes |")
    print("|---:|---:|---:|---:|---:|")
    with tempfile.TemporaryDirectory(prefix="stp-bench-") as temp:
        root = Path(temp)
        for steps in STEP_COUNTS:
            no_cp: list[float] = []
            with_cp: list[float] = []
            size = 0
            for repeat in range(REPEATS):
                task_obj = build_task(steps)
                elapsed, run = await timed_run(task_obj)
                assert run.status.value == "SUCCEEDED"
                no_cp.append(elapsed)

                directory = root / f"cp-{steps}-{repeat}"
                store = JsonCheckpointStore(directory)
                task_obj = build_task(steps)
                elapsed, run = await timed_run(task_obj, store)
                assert run.status.value == "SUCCEEDED"
                with_cp.append(elapsed)
                size = checkpoint_bytes(directory)

            no_cp_median = statistics.median(no_cp)
            cp_median = statistics.median(with_cp)
            print(
                f"| {steps} | {no_cp_median:.2f} | {cp_median:.2f} | "
                f"{cp_median - no_cp_median:.2f} | {size} |"
            )

        print("\n## Resume benchmark\n")
        print("| steps | first run ms | resume ms | duplicate completed calls |")
        print("|---:|---:|---:|---:|")
        for steps in STEP_COUNTS:
            fail_at = steps // 2
            calls: dict[str, int] = {}
            directory = root / f"resume-{steps}"
            store = JsonCheckpointStore(directory)
            task_obj = build_task(steps, calls=calls, fail_at=fail_at)

            first_ms, first_run = await timed_run(task_obj, store)
            assert first_run.status.value == "FAILED"

            started = time.perf_counter()
            resumed = await task_obj.resume(
                run_id=first_run.run_id,
                runtime=TaskRuntime(checkpoint_store=store),
            )
            resume_ms = (time.perf_counter() - started) * 1000
            assert resumed.status.value == "SUCCEEDED"

            duplicate_completed = sum(
                max(0, count - 1)
                for name, count in calls.items()
                if int(name[1:]) < fail_at
            )
            print(
                f"| {steps} | {first_ms:.2f} | {resume_ms:.2f} | "
                f"{duplicate_completed} |"
            )


if __name__ == "__main__":
    asyncio.run(main())
