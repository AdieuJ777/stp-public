"""E2：恢复有效性实验。

运行：
    cd stp
    uv run python bench/experiments/e2_recovery.py

指标：
- 安全恢复成功率；
- 危险场景正确拒绝率；
- 已完成步骤重复执行率；
- 恢复耗时；
- 恢复输出与不中断参考执行的一致性。
"""
from __future__ import annotations

import asyncio
import csv
import json
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from stp import (
    FrameworkError,
    JsonCheckpointStore,
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

from common import default_results_dir, env_metadata, write_env

def _linear_spec(task_id: str, *, steps: int = 5) -> TaskSpec:
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
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        retry_policy=RetryPolicy(
            max_attempts=1,
            backoff_factor=1.0,
            initial_delay_seconds=0.001,
        ),
        max_steps=steps + 5,
        timeout_seconds=10.0,
    )


def _status_spec(task_id: str) -> TaskSpec:
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
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
        retry_policy=RetryPolicy(
            max_attempts=1,
            backoff_factor=1.0,
            initial_delay_seconds=0.001,
        ),
        max_steps=20,
        timeout_seconds=10.0,
    )


def _result_spec(task_id: str) -> TaskSpec:
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
        retry_policy=RetryPolicy(
            max_attempts=1,
            backoff_factor=1.0,
            initial_delay_seconds=0.001,
        ),
        max_steps=20,
        timeout_seconds=10.0,
    )


def _empty_tools(calls: dict, fail_first: bool) -> ToolRegistry:
    return ToolRegistry()


def _linear_task(
    spec: TaskSpec,
    calls: dict[str, int],
    fail_first: bool,
    *,
    steps: int = 5,
    fail_step: str = "s3",
):
    def make_step(index: int):
        name = f"s{index}"

        def fn(_data):
            calls[name] = calls.get(name, 0) + 1
            if fail_first and name == fail_step and calls[name] == 1:
                raise FrameworkError(f"injected failure at {name}")
            return {"value": index}

        return fn

    @task(spec)
    def build(ctx):
        for index in range(1, steps + 1):
            ctx.step(
                f"s{index}",
                func=make_step(index),
                input={},
                output_key=f"s{index}",
            )
        ctx.end({"value": ctx.state[f"s{steps}"]["value"]})

    return build


def _branch_task(
    spec: TaskSpec,
    calls: dict[str, int],
    fail_first: bool,
    route_holder: dict[str, str],
):
    def classify(_data):
        calls["classify"] = calls.get("classify", 0) + 1
        return {"amount": 1200}

    def high(_data):
        calls["high"] = calls.get("high", 0) + 1
        if fail_first and calls["high"] == 1:
            raise FrameworkError("injected failure at high")
        return {"status": "approved"}

    def low(_data):
        calls["low"] = calls.get("low", 0) + 1
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


def _nested_task(
    spec: TaskSpec,
    calls: dict[str, int],
    fail_first: bool,
):
    def classify(_data):
        calls["classify"] = calls.get("classify", 0) + 1
        return {"amount": 1500, "region": "EU"}

    def high_eu(_data):
        calls["high_eu"] = calls.get("high_eu", 0) + 1
        if fail_first and calls["high_eu"] == 1:
            raise FrameworkError("injected failure at high_eu")
        return {"result": "high-eu"}

    def high_other(_data):
        calls["high_other"] = calls.get("high_other", 0) + 1
        return {"result": "high-other"}

    def low(_data):
        calls["low"] = calls.get("low", 0) + 1
        return {"result": "low"}

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
                condition=lambda state: "eu",
                cases={"eu": eu, "other": other},
                default="other",
            )

        def low_case(c):
            c.step("low", func=low, input={}, output_key="result")

        ctx.branch(
            "outer",
            condition=lambda state: "high",
            cases={"high": high, "low": low_case},
            default="low",
        )
        ctx.end({"result": ctx.state["result"]["result"]})

    return build


class _FlakyTool(Tool):
    spec = ToolSpec(
        tool_name="flaky_tool",
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

    def __init__(self, calls: dict[str, int], fail_first: bool) -> None:
        self.calls = calls
        self.fail_first = fail_first

    async def execute(self, input_data, context):
        self.calls["tool"] = self.calls.get("tool", 0) + 1
        if self.fail_first and self.calls["tool"] == 1:
            raise ToolExecutionError("injected tool failure")
        return {"result": "ok"}


def _tool_task(spec: TaskSpec, calls: dict[str, int], fail_first: bool):
    def pre(_data):
        calls["pre"] = calls.get("pre", 0) + 1
        return {"result": "pre"}

    @task(spec)
    def build(ctx):
        ctx.step("pre", func=pre, input={}, output_key="pre")
        ctx.step("tool", tool="flaky_tool", input={}, output_key="tool")
        ctx.end({"result": ctx.state["tool"]["result"]})

    return build


def _tool_registry(calls: dict[str, int], fail_first: bool) -> ToolRegistry:
    return ToolRegistry([_FlakyTool(calls, fail_first)])


@dataclass
class SafeCase:
    name: str
    description: str
    spec: TaskSpec
    make_task: Callable[[dict, bool], Any]
    make_tools: Callable[[dict, bool], ToolRegistry]
    input_data: dict[str, Any]


def _safe_cases() -> list[SafeCase]:
    linear_spec = _linear_spec("TSK-E2-LINEAR")
    branch_spec = _status_spec("TSK-E2-BRANCH")
    nested_spec = _result_spec("TSK-E2-NESTED")
    tool_spec = _result_spec("TSK-E2-TOOL")

    return [
        SafeCase(
            name="linear_mid",
            description="5 步线性任务，第 3 步首次失败",
            spec=linear_spec,
            make_task=lambda calls, fail: _linear_task(
                linear_spec, calls, fail, steps=5, fail_step="s3"
            ),
            make_tools=_empty_tools,
            input_data={"x": 1},
        ),
        SafeCase(
            name="branch_high",
            description="单层分支 high 步骤首次失败",
            spec=branch_spec,
            make_task=lambda calls, fail: _branch_task(
                branch_spec, calls, fail, {"route": "high"}
            ),
            make_tools=_empty_tools,
            input_data={"x": 1},
        ),
        SafeCase(
            name="nested_high_eu",
            description="嵌套分支 high_eu 步骤首次失败",
            spec=nested_spec,
            make_task=lambda calls, fail: _nested_task(nested_spec, calls, fail),
            make_tools=_empty_tools,
            input_data={"x": 1},
        ),
        SafeCase(
            name="tool_first_failure",
            description="工具步骤首次失败，恢复后成功",
            spec=tool_spec,
            make_task=lambda calls, fail: _tool_task(tool_spec, calls, fail),
            make_tools=_tool_registry,
            input_data={"x": 1},
        ),
    ]


def _run_safe(case: SafeCase, root: Path) -> dict[str, Any]:
    ref_calls: dict[str, int] = {}
    ref_task = case.make_task(ref_calls, False)
    ref_runtime = TaskRuntime(tools=case.make_tools(ref_calls, False))
    ref_run = asyncio.run(ref_task.run(case.input_data, runtime=ref_runtime))
    assert ref_run.status == TaskStatus.SUCCEEDED, ref_run.error

    calls: dict[str, int] = {}
    task_obj = case.make_task(calls, True)
    store = JsonCheckpointStore(root / case.name)
    runtime = TaskRuntime(
        tools=case.make_tools(calls, True),
        checkpoint_store=store,
    )

    first = asyncio.run(task_obj.run(case.input_data, runtime=runtime))
    assert first.status == TaskStatus.FAILED, first.error
    latest = store.load_latest(first.run_id, case.spec.task_id)
    assert latest is not None

    plan = task_obj.build()
    completed_step_ids = [
        node_id
        for node_id in latest.completed_steps
        if plan.nodes[node_id].kind == "step"
    ]
    completed_names = [plan.nodes[node_id].name for node_id in completed_step_ids]

    started = time.perf_counter()
    resumed = asyncio.run(task_obj.resume(run_id=first.run_id, runtime=runtime))
    resume_ms = (time.perf_counter() - started) * 1000

    re_executed = sum(
        max(0, calls.get(name, 0) - 1) for name in completed_names
    )
    duplicate_rate = (
        re_executed / len(completed_names) if completed_names else 0.0
    )
    output_consistent = (
        resumed.status == TaskStatus.SUCCEEDED and resumed.output == ref_run.output
    )

    return {
        "case": case.name,
        "description": case.description,
        "reference_status": ref_run.status.value,
        "first_status": first.status.value,
        "first_error": first.error.code if first.error else "",
        "resume_status": resumed.status.value,
        "resume_error": resumed.error.code if resumed.error else "",
        "output_consistent": output_consistent,
        "completed_step_count": len(completed_names),
        "completed_steps": ";".join(completed_names),
        "re_executed_completed": re_executed,
        "duplicate_rate": f"{duplicate_rate:.3f}",
        "resume_ms": f"{resume_ms:.2f}",
        "reference_output": json.dumps(ref_run.output, ensure_ascii=False, sort_keys=True),
        "resumed_output": json.dumps(resumed.output, ensure_ascii=False, sort_keys=True)
        if resumed.output is not None
        else "",
        "calls": json.dumps(calls, ensure_ascii=False, sort_keys=True),
    }


def _attempt_resume(
    task_obj,
    runtime: TaskRuntime,
    first,
    *,
    input_data: dict | None = None,
) -> tuple[str, str, str, float]:
    started = time.perf_counter()
    try:
        resumed = asyncio.run(
            task_obj.resume(
                run_id=first.run_id,
                runtime=runtime,
                input_data=input_data,
            )
        )
        elapsed = (time.perf_counter() - started) * 1000
        outcome = "succeeded" if resumed.status == TaskStatus.SUCCEEDED else "failed"
        code = resumed.error.code if resumed.error else ""
        return outcome, code, code, elapsed
    except FrameworkError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return "refused", exc.code, exc.code, elapsed


class _NonIdempotentTool(Tool):
    spec = ToolSpec(
        tool_name="danger_tool",
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
        side_effect=True,
        idempotent=False,
    )

    async def execute(self, input_data, context):
        raise ToolExecutionError("external side effect state is unknown")


def _danger_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # 1. 非幂等副作用：必须拒绝自动恢复
    spec = _result_spec("TSK-E2-DANGER-TOOL")
    # 非幂等副作用工具：直接写一个独立任务
    @task(spec)
    def build_danger(ctx):
        ctx.step("danger", tool="danger_tool", input={}, output_key="v")
        ctx.end({"result": ctx.state["v"]["result"]})

    runtime = TaskRuntime(
        tools=ToolRegistry([_NonIdempotentTool()]),
        checkpoint_store=JsonCheckpointStore(root / "non_idempotent"),
    )
    first = asyncio.run(build_danger.run({"x": 1}, runtime=runtime))
    row = _danger_row(
        "non_idempotent_unknown",
        "非幂等副作用工具失败，应拒绝自动恢复",
        "E_RESUME_UNSUPPORTED",
        first,
        build_danger,
        runtime,
    )
    rows.append(row)

    # 2. 损坏检查点
    spec = _linear_spec("TSK-E2-DANGER-CORRUPT")
    calls = {}
    task_obj = _linear_task(spec, calls, True, steps=5, fail_step="s3")
    store = JsonCheckpointStore(root / "corrupt")
    runtime = TaskRuntime(checkpoint_store=store)
    first = asyncio.run(task_obj.run({"x": 1}, runtime=runtime))
    latest = store.load_latest(first.run_id, spec.task_id)
    assert latest is not None
    path = store.base_dir / first.run_id / f"{latest.sequence:06d}.json"
    path.write_text("{not valid json", encoding="utf-8")
    rows.append(
        _danger_row(
            "corrupt_checkpoint",
            "最新检查点被破坏，应拒绝恢复",
            "E_CHECKPOINT_INVALID",
            first,
            task_obj,
            runtime,
        )
    )

    # 3. 计划变化
    spec = _linear_spec("TSK-E2-DANGER-PLAN")
    calls = {}
    task_obj = _linear_task(spec, calls, True, steps=5, fail_step="s3")
    store = JsonCheckpointStore(root / "changed-plan")
    runtime = TaskRuntime(checkpoint_store=store)
    first = asyncio.run(task_obj.run({"x": 1}, runtime=runtime))
    new_task = _linear_task(spec, {}, False, steps=6)
    rows.append(
        _danger_row(
            "changed_plan",
            "恢复时计划结构变化，应拒绝",
            "E_CHECKPOINT_INCOMPATIBLE",
            first,
            new_task,
            runtime,
        )
    )

    # 4. 输入变化
    spec = _linear_spec("TSK-E2-DANGER-INPUT")
    calls = {}
    task_obj = _linear_task(spec, calls, True, steps=5, fail_step="s3")
    store = JsonCheckpointStore(root / "changed-input")
    runtime = TaskRuntime(checkpoint_store=store)
    first = asyncio.run(task_obj.run({"x": 1}, runtime=runtime))
    rows.append(
        _danger_row(
            "changed_input",
            "恢复时输入变化，应拒绝",
            "E_CHECKPOINT_INCOMPATIBLE",
            first,
            task_obj,
            runtime,
            input_data={"x": 2},
        )
    )

    # 5. 分支路由变化
    spec = _status_spec("TSK-E2-DANGER-ROUTE")
    route_holder = {"route": "high"}
    calls = {}
    task_obj = _branch_task(spec, calls, True, route_holder)
    store = JsonCheckpointStore(root / "route-mismatch")
    runtime = TaskRuntime(checkpoint_store=store)
    first = asyncio.run(task_obj.run({"x": 1}, runtime=runtime))
    route_holder["route"] = "low"
    rows.append(
        _danger_row(
            "route_mismatch",
            "恢复时分支条件给出不同路由，应拒绝",
            "E_RESUME_UNSUPPORTED",
            first,
            task_obj,
            runtime,
        )
    )

    return rows


def _danger_row(
    name: str,
    description: str,
    expected_code: str,
    first,
    task_obj,
    runtime: TaskRuntime,
    *,
    input_data: dict | None = None,
) -> dict[str, Any]:
    outcome, code, error_code, elapsed = _attempt_resume(
        task_obj, runtime, first, input_data=input_data
    )
    return {
        "case": name,
        "description": description,
        "expected_code": expected_code,
        "outcome": outcome,
        "error_code": error_code,
        "correct": code == expected_code and outcome != "succeeded",
        "first_status": first.status.value,
        "first_error": first.error.code if first.error else "",
        "resume_ms": f"{elapsed:.2f}",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    path: Path,
    safe_rows: list[dict[str, Any]],
    danger_rows: list[dict[str, Any]],
    env: dict[str, Any],
) -> None:
    safe_total = len(safe_rows)
    safe_success = sum(
        1
        for row in safe_rows
        if row["resume_status"] == "SUCCEEDED"
        and row["output_consistent"]
        and row["re_executed_completed"] == 0
    )
    safe_rate = safe_success / safe_total * 100 if safe_total else 0.0
    completed_total = sum(row["completed_step_count"] for row in safe_rows)
    re_executed_total = sum(row["re_executed_completed"] for row in safe_rows)
    duplicate_rate = (
        re_executed_total / completed_total * 100 if completed_total else 0.0
    )
    consistency = (
        sum(1 for row in safe_rows if row["output_consistent"]) / safe_total * 100
        if safe_total
        else 0.0
    )
    resume_median = (
        statistics.median(float(row["resume_ms"]) for row in safe_rows)
        if safe_rows
        else 0.0
    )

    danger_total = len(danger_rows)
    danger_correct = sum(1 for row in danger_rows if row["correct"])
    danger_rate = danger_correct / danger_total * 100 if danger_total else 0.0

    lines = [
        "# E2 恢复有效性实验结果",
        "",
        f"- Python：{env['python']}",
        f"- 平台：{env['platform']}",
        f"- stp：{env['stp_version']}",
        f"- git commit：{env['git_commit']}{'（dirty）' if env['git_dirty'] else ''}",
        "",
        "## 总体指标",
        "",
        f"- 安全恢复成功率：{safe_success}/{safe_total}（{safe_rate:.1f}%）",
        f"- 恢复输出一致性：{consistency:.1f}%",
        f"- 已完成步骤重复执行率：{re_executed_total}/{completed_total}（{duplicate_rate:.1f}%）",
        f"- 安全场景恢复耗时中位数：{resume_median:.2f} ms",
        f"- 危险场景正确拒绝率：{danger_correct}/{danger_total}（{danger_rate:.1f}%）",
        "",
        "## 安全恢复",
        "",
        "| 场景 | 说明 | 首次状态 | 恢复状态 | 输出一致 | 已完成步骤 | 重复执行 | 恢复 ms |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for row in safe_rows:
        lines.append(
            f"| {row['case']} | {row['description']} | {row['first_status']} | "
            f"{row['resume_status']} | {'✅' if row['output_consistent'] else '❌'} | "
            f"{row['completed_step_count']} | {row['re_executed_completed']} | "
            f"{row['resume_ms']} |"
        )
    lines.extend(
        [
            "",
            "## 危险场景",
            "",
            "| 场景 | 说明 | 期望错误码 | 实际结果 | 实际错误码 | 正确 | 恢复 ms |",
            "|---|---|---|---|---|---|---:|",
        ]
    )
    for row in danger_rows:
        lines.append(
            f"| {row['case']} | {row['description']} | {row['expected_code']} | "
            f"{row['outcome']} | {row['error_code']} | "
            f"{'✅' if row['correct'] else '❌'} | {row['resume_ms']} |"
        )
    lines.extend(
        [
            "",
            "> “已完成步骤重复执行率”只统计 step 节点；branch 节点按设计会在恢复时重新求值，不计入重复执行。",
            "> 安全恢复成功要求：恢复后 SUCCEEDED、输出与不中断参考一致、已完成步骤重复执行数为 0。",
            "> 危险场景包括：非幂等副作用 UNKNOWN、损坏检查点、计划变化、输入变化、分支路由变化。",
            "> `route_mismatch` 在运行期检测到路由变化，因此表现为 `FAILED` + `E_RESUME_UNSUPPORTED`；其余危险场景在恢复预检查阶段被拒绝。",
            "> 数字用于报告量级，不是性能承诺。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    results_dir = default_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    env = env_metadata()

    with tempfile.TemporaryDirectory(prefix="stp-e2-") as temp:
        root = Path(temp)
        safe_rows = []
        for case in _safe_cases():
            row = _run_safe(case, root)
            safe_rows.append(row)
            print(
                f"  safe={case.name:<20} "
                f"first={row['first_status']:<8} resume={row['resume_status']:<9} "
                f"duplicate={row['re_executed_completed']} "
                f"resume={row['resume_ms']}ms"
            )
        danger_rows = _danger_rows(root)
        for row in danger_rows:
            print(
                f"  danger={row['case']:<22} "
                f"outcome={row['outcome']:<8} code={row['error_code']:<28} "
                f"correct={row['correct']}"
            )

    _write_csv(results_dir / "e2_recovery.csv", safe_rows)
    _write_csv(results_dir / "e2_safety.csv", danger_rows)
    _write_markdown(results_dir / "e2_summary.md", safe_rows, danger_rows, env)
    write_env(results_dir / "env.json", env)

    print(f"\nresults: {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
