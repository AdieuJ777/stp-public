"""E6：运行 STP 与 LangGraph 对照实验。

运行：
    cd stp
    uv sync --extra dev --extra e6
    uv run python bench/e6_langgraph/run_compare.py

输出：
    bench/e6_langgraph/results/e6_comparison.csv
    bench/e6_langgraph/results/e6_summary.md
    bench/e6_langgraph/results/env.json
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

# 复用 experiments 的环境元数据工具
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from common import env_metadata, write_env  # noqa: E402

from shared import T1_EXPECTED, T2_EXPECTED  # noqa: E402

import langgraph_t1  # noqa: E402
import langgraph_t2  # noqa: E402
import stp_t1_graph  # noqa: E402
import stp_t1_script  # noqa: E402
import stp_t2  # noqa: E402


RESULTS = Path(__file__).resolve().parent / "results"


def _run_safe(fn) -> tuple[str, Any, str]:
    try:
        return "SUCCEEDED", fn(), ""
    except Exception as exc:  # 让一个实现失败不终止整批实验
        return "FAILED", None, f"{type(exc).__name__}: {exc}"


def _duplicate_completed(calls: dict[str, int], completed: list[str]) -> int:
    return sum(max(0, calls.get(name, 0) - 1) for name in completed)


def _t1_rows() -> list[dict[str, Any]]:
    rows = []
    for name, fn in (
        ("STP script", stp_t1_script.run),
        ("STP graph", stp_t1_graph.run),
        ("LangGraph", langgraph_t1.run),
    ):
        status, output, error = _run_safe(fn)
        rows.append(
            {
                "task": "T1",
                "implementation": name,
                "first_status": status,
                "resume_status": "",
                "output_ok": output == T1_EXPECTED,
                "duplicate_completed": "",
                "calls": "",
                "error": error,
            }
        )
    return rows


def _t2_rows() -> list[dict[str, Any]]:
    rows = []
    for name, fn in (
        ("STP", stp_t2.run),
        ("LangGraph", langgraph_t2.run),
    ):
        try:
            result = fn()
        except Exception as exc:
            rows.append(
                {
                    "task": "T2",
                    "implementation": name,
                    "first_status": "CRASH",
                    "resume_status": "",
                    "output_ok": False,
                    "duplicate_completed": "",
                    "calls": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        calls = result["calls"]
        duplicate = _duplicate_completed(calls, ["s1", "s2"])
        rows.append(
            {
                "task": "T2",
                "implementation": name,
                "first_status": result["first_status"],
                "resume_status": result["resume_status"],
                "output_ok": result["output"] == T2_EXPECTED,
                "duplicate_completed": duplicate,
                "calls": json.dumps(calls, ensure_ascii=False, sort_keys=True),
                "error": "",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, Any]], env: dict[str, Any]) -> None:
    lines = [
        "# E6 STP vs LangGraph 对照",
        "",
        f"- Python：{env['python']}",
        f"- 平台：{env['platform']}",
        f"- stp：{env['stp_version']}",
        f"- git commit：{env['git_commit']}{'（dirty）' if env['git_dirty'] else ''}",
        "",
        "| 任务 | 实现 | 首次状态 | 恢复状态 | 输出符合预期 | 重复已完成步骤 | 错误 |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['task']} | {row['implementation']} | {row['first_status']} | "
            f"{row['resume_status'] or '—'} | {'✅' if row['output_ok'] else '❌'} | "
            f"{row['duplicate_completed'] if row['duplicate_completed'] != '' else '—'} | "
            f"{row['error']} |"
        )
    lines.extend(
        [
            "",
            "> T1：审批/记账正常路径，比较三种实现的可观察输出。",
            "> T2：第 3 步首次失败后恢复；重复已完成步骤只统计 s1、s2。",
            "> 业务函数（抽取、预算、审批、记账）由 `shared.py` 提供，不计入各实现的编排 LOC。",
            "> 这是一次单开发者、小任务的对照，不声称 STP 普遍优于 LangGraph。",
            "> 代码 LOC 与结构指标见 `e6_loc.csv`（由 `measure.py` 生成）。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    env = env_metadata()
    rows = _t1_rows() + _t2_rows()

    _write_csv(RESULTS / "e6_comparison.csv", rows)
    _write_markdown(RESULTS / "e6_summary.md", rows, env)
    write_env(RESULTS / "env.json", env)

    for row in rows:
        print(
            f"  {row['task']} {row['implementation']:<12} "
            f"first={row['first_status']:<9} resume={row['resume_status'] or '-':<9} "
            f"output_ok={row['output_ok']} duplicate={row['duplicate_completed']}"
        )
    print(f"\nresults: {RESULTS}")
    return 0 if all(row["output_ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
