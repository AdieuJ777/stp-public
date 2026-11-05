"""E4：静态计划校验有效性实验。

运行：
    cd stp
    uv run python bench/experiments/e4_validation.py

对 7 类非法计划调用 validate_plan()，统计：
- 是否在执行前被拦截；
- 是否命中预期错误；
- 错误码；
- 合法基线是否被误报。
"""
from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from stp import GraphBuilder, PlanValidationError, TaskIR, TaskRuntime, TaskSpec
from stp.refs import Ref
from stp.validation import validate_plan

from common import default_results_dir, env_metadata, write_env


def _spec(task_id: str, *, max_steps: int = 10) -> TaskSpec:
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
        max_steps=max_steps,
        timeout_seconds=10.0,
    )


def _valid_plan(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    a = builder.add_step("a", func=lambda _: {"value": 1}, input={}, output_key="a")
    b = builder.add_step("b", func=lambda _: {"value": 2}, input={}, output_key="b")
    end = builder.add_end("end", {"value": Ref("state", "b", ("value",))})
    builder.set_entry(a)
    builder.add_edge(a, b)
    builder.add_edge(b, end)
    return builder.compile()


def _dangling_reference(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    bad = builder.add_step(
        "bad",
        func=lambda _: {"value": 1},
        input={"value": Ref("state", "missing", ("value",))},
        output_key="bad",
    )
    end = builder.add_end("end", {"value": Ref("state", "bad", ("value",))})
    builder.set_entry(bad)
    builder.add_edge(bad, end)
    return builder.compile()


def _cycle(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    a = builder.add_step("a", func=lambda _: {"value": 1}, input={}, output_key="a")
    b = builder.add_step("b", func=lambda _: {"value": 2}, input={}, output_key="b")
    end = builder.add_end("end", {"value": Ref("state", "b", ("value",))})
    builder.set_entry(a)
    builder.add_edge(a, b)
    builder.add_edge(b, a)
    return builder.compile()


def _unreachable(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    a = builder.add_step("a", func=lambda _: {"value": 1}, input={}, output_key="a")
    b = builder.add_step("b", func=lambda _: {"value": 2}, input={}, output_key="b")
    orphan = builder.add_step(
        "orphan", func=lambda _: {"value": 3}, input={}, output_key="orphan"
    )
    end = builder.add_end("end", {"value": Ref("state", "b", ("value",))})
    builder.set_entry(a)
    builder.add_edge(a, b)
    builder.add_edge(b, end)
    builder.add_edge(orphan, end)
    return builder.compile()


def _branch_without_default(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    start = builder.add_step("start", func=lambda _: {"value": 0}, input={}, output_key="start")
    branch = builder.add_branch("route", condition=lambda state: "high", default="missing")
    high = builder.add_step("high", func=lambda _: {"value": 1}, input={}, output_key="value")
    low = builder.add_step("low", func=lambda _: {"value": 2}, input={}, output_key="value")
    join = builder.add_join("join")
    end = builder.add_end("end", {"value": Ref("state", "value", ("value",))})
    builder.set_entry(start)
    builder.add_edge(start, branch)
    builder.add_route(branch, "high", high)
    builder.add_route(branch, "low", low)
    builder.add_edge(high, join)
    builder.add_edge(low, join)
    builder.add_edge(join, end)
    return builder.compile()


def _duplicate_output_key(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    a = builder.add_step("a", func=lambda _: {"value": 1}, input={}, output_key="dup")
    b = builder.add_step("b", func=lambda _: {"value": 2}, input={}, output_key="dup")
    end = builder.add_end("end", {"value": Ref("state", "dup", ("value",))})
    builder.set_entry(a)
    builder.add_edge(a, b)
    builder.add_edge(b, end)
    return builder.compile()


def _end_missing_required(spec: TaskSpec) -> TaskIR:
    builder = GraphBuilder(spec)
    a = builder.add_step("a", func=lambda _: {"value": 1}, input={}, output_key="a")
    end = builder.add_end("end", {})
    builder.set_entry(a)
    builder.add_edge(a, end)
    return builder.compile()


def _max_steps_too_small(spec: TaskSpec) -> TaskIR:
    return _valid_plan(spec)


@dataclass
class InvalidCase:
    name: str
    description: str
    expected: str
    spec: TaskSpec
    build_plan: Callable[[TaskSpec], TaskIR]


def _cases() -> list[InvalidCase]:
    return [
        InvalidCase(
            "dangling_reference",
            "步骤引用了不存在的 state key",
            "unknown state key",
            _spec("TSK-E4-DANGLING"),
            _dangling_reference,
        ),
        InvalidCase(
            "cycle",
            "计划存在环",
            "cycle",
            _spec("TSK-E4-CYCLE"),
            _cycle,
        ),
        InvalidCase(
            "unreachable",
            "存在从 entry 不可达的节点",
            "unreachable",
            _spec("TSK-E4-UNREACHABLE"),
            _unreachable,
        ),
        InvalidCase(
            "branch_without_default",
            "分支 default 不在 routes 中",
            "default_route",
            _spec("TSK-E4-BRANCH"),
            _branch_without_default,
        ),
        InvalidCase(
            "duplicate_output_key",
            "同一路径上重复 output_key",
            "duplicate output_key",
            _spec("TSK-E4-DUP"),
            _duplicate_output_key,
        ),
        InvalidCase(
            "end_missing_required",
            "end 输出映射缺少必填字段",
            "missing required fields",
            _spec("TSK-E4-END"),
            _end_missing_required,
        ),
        InvalidCase(
            "max_steps_too_small",
            "max_steps 小于最短路径步数",
            "max_steps",
            _spec("TSK-E4-MAXSTEPS", max_steps=1),
            _max_steps_too_small,
        ),
    ]


def _run_case(case: InvalidCase) -> dict[str, Any]:
    plan = case.build_plan(case.spec)
    report = validate_plan(case.spec, plan)
    errors = list(report.errors)
    matched = any(case.expected.lower() in error.lower() for error in errors)

    error_code = ""
    try:
        report.raise_if_errors()
    except PlanValidationError as exc:
        error_code = exc.code

    runtime_rejected = False
    runtime_error = ""
    try:
        asyncio.run(TaskRuntime().run(case.spec, plan, {"x": 1}))
    except PlanValidationError as exc:
        runtime_rejected = True
        runtime_error = exc.code
    except Exception as exc:  # 记录非预期错误，不让整批实验中断
        runtime_error = f"{type(exc).__name__}: {exc}"

    return {
        "case": case.name,
        "description": case.description,
        "expected": case.expected,
        "caught": bool(errors),
        "matched": matched,
        "error_code": error_code,
        "runtime_rejected": runtime_rejected,
        "runtime_error": runtime_error,
        "errors": " | ".join(errors),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, Any]], env: dict[str, Any]) -> None:
    invalid_rows = [row for row in rows if row["case"] != "valid_baseline"]
    valid_rows = [row for row in rows if row["case"] == "valid_baseline"]
    total = len(invalid_rows)
    caught = sum(1 for row in invalid_rows if row["caught"])
    matched = sum(1 for row in invalid_rows if row["matched"])
    runtime_rejected = sum(1 for row in invalid_rows if row["runtime_rejected"])
    valid_false_positive = sum(1 for row in valid_rows if row["caught"])

    lines = [
        "# E4 静态计划校验有效性",
        "",
        f"- Python：{env['python']}",
        f"- 平台：{env['platform']}",
        f"- stp：{env['stp_version']}",
        f"- git commit：{env['git_commit']}{'（dirty）' if env['git_dirty'] else ''}",
        "",
        f"- 注入非法计划：{total}",
        f"- validate_plan 检出：{caught}/{total}（{caught / total * 100:.1f}%）",
        f"- 命中预期错误：{matched}/{total}（{matched / total * 100:.1f}%）",
        f"- TaskRuntime 执行前拒绝：{runtime_rejected}/{total}（{runtime_rejected / total * 100:.1f}%）",
        f"- 合法基线误报：{valid_false_positive}",
        "",
        "| 用例 | 说明 | 预期错误 | 检出 | 命中 | 错误码 | 运行时拒绝 | 错误列表 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in invalid_rows:
        lines.append(
            f"| {row['case']} | {row['description']} | {row['expected']} | "
            f"{'✅' if row['caught'] else '❌'} | {'✅' if row['matched'] else '❌'} | "
            f"{row['error_code']} | {'✅' if row['runtime_rejected'] else '❌'} | "
            f"{row['errors']} |"
        )
    lines.extend(
        [
            "",
            f"- 合法基线：`validate_plan` 错误 = {(valid_rows[0]['errors'] if valid_rows else '') or '无'}；误报 {valid_false_positive} 次。",
            "",
            "> “运行时拒绝”表示 `TaskRuntime.run()` 在创建 TaskRun 之前抛出 `PlanValidationError`，"
            "即错误不会进入执行阶段。",
            "> 本实验只覆盖静态结构错误；业务规则验证器失败属于运行期 `E_BUSINESS_RULE`，不计入拦截率。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    results_dir = default_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    env = env_metadata()

    rows = []
    for case in _cases():
        row = _run_case(case)
        rows.append(row)
        print(
            f"  {case.name:<26} caught={row['caught']} "
            f"matched={row['matched']} runtime_rejected={row['runtime_rejected']}"
        )

    # 合法基线：不能被误报
    valid_spec = _spec("TSK-E4-VALID")
    valid_report = validate_plan(valid_spec, _valid_plan(valid_spec))
    valid_row = {
        "case": "valid_baseline",
        "description": "合法基线计划，不应被拒绝",
        "expected": "no error",
        "caught": bool(valid_report.errors),
        "matched": not valid_report.errors,
        "error_code": "",
        "runtime_rejected": False,
        "runtime_error": "",
        "errors": " | ".join(valid_report.errors),
    }
    rows.append(valid_row)
    print(f"  {'valid_baseline':<26} errors={valid_report.errors}")

    _write_csv(results_dir / "e4_validation.csv", rows)
    _write_markdown(results_dir / "e4_summary.md", rows, env)
    write_env(results_dir / "env.json", env)
    print(f"\nresults: {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
