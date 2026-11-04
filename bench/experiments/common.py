"""实验公共设施：场景执行、指标行、CSV/Markdown 输出、环境元数据。"""
from __future__ import annotations

import asyncio
import csv
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stp import TaskRuntime
from stp.diff import compare_runs

from scenarios import Scenario


def repo_root() -> Path:
    # bench/experiments/common.py -> stp/
    return Path(__file__).resolve().parents[2]


def default_results_dir() -> Path:
    return Path(__file__).resolve().parent / "results"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def env_metadata() -> dict[str, Any]:
    try:
        version = importlib.metadata.version("stp")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "stp_version": version,
        "git_commit": _git("rev-parse", "HEAD") or "unknown",
        "git_dirty": bool(_git("status", "--porcelain")),
        "cwd": str(Path.cwd()),
    }


def _run_one(task_obj, runtime: TaskRuntime, input_data: dict, kwargs: dict) -> Any:
    try:
        return asyncio.run(task_obj.run(input_data, runtime=runtime, **kwargs))
    except Exception as exc:  # 实验脚本保留失败现场，不中断整批场景
        return exc


def _status(value: Any) -> str:
    return "CRASH" if isinstance(value, BaseException) else value.status.value


def _error(value: Any) -> str:
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return value.error.code if value.error is not None else ""


def run_scenario(scenario: Scenario) -> dict[str, Any]:
    script_task, script_kwargs = scenario.make_script()
    graph_task, graph_kwargs = scenario.make_graph()

    script_runtime = TaskRuntime(
        backend=scenario.make_backend(),
        tools=scenario.make_tools(),
    )
    graph_runtime = TaskRuntime(
        backend=scenario.make_backend(),
        tools=scenario.make_tools(),
    )

    left = _run_one(script_task, script_runtime, scenario.input_data, script_kwargs)
    right = _run_one(graph_task, graph_runtime, scenario.input_data, graph_kwargs)

    row: dict[str, Any] = {
        "scenario": scenario.name,
        "description": scenario.description,
        "strict_fields": ",".join(scenario.strict_fields),
        "left_status": _status(left),
        "right_status": _status(right),
        "left_error": _error(left),
        "right_error": _error(right),
        "equivalent": False,
        "differences": "",
        "left_retries": "",
        "right_retries": "",
        "left_tools": "",
        "right_tools": "",
        "left_output": "",
        "right_output": "",
    }

    if isinstance(left, BaseException) or isinstance(right, BaseException):
        row["differences"] = (
            f"left_crash={left!r}" if isinstance(left, BaseException) else ""
        ) + (
            f" right_crash={right!r}" if isinstance(right, BaseException) else ""
        )
        return row

    report = compare_runs(
        left,
        right,
        left_events=script_runtime.events_for(left.run_id),
        right_events=graph_runtime.events_for(right.run_id),
        strict_fields=list(scenario.strict_fields),
    )
    row.update(
        {
            "equivalent": report.equivalent,
            "differences": " | ".join(report.differences),
            "left_retries": report.observations.get("left_retries", ""),
            "right_retries": report.observations.get("right_retries", ""),
            "left_tools": ";".join(report.observations.get("left_tools", [])),
            "right_tools": ";".join(report.observations.get("right_tools", [])),
            "left_output": json.dumps(left.output, ensure_ascii=False, sort_keys=True)
            if left.output is not None
            else "",
            "right_output": json.dumps(right.output, ensure_ascii=False, sort_keys=True)
            if right.output is not None
            else "",
        }
    )
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, Any]], env: dict[str, Any]) -> None:
    total = len(rows)
    equivalent = sum(1 for row in rows if row["equivalent"])
    rate = (equivalent / total * 100) if total else 0.0
    lines = [
        "# E1 双模式等价性实验结果",
        "",
        f"- 场景数：{total}",
        f"- 等价场景数：{equivalent}",
        f"- 等价率：{rate:.1f}%",
        f"- Python：{env['python']}",
        f"- 平台：{env['platform']}",
        f"- stp：{env['stp_version']}",
        f"- git commit：{env['git_commit']}{'（dirty）' if env['git_dirty'] else ''}",
        f"- 时间：{env['timestamp_utc']}",
        "",
        "> 等价标准：最终状态、错误码、指定输出字段、重试次数、工具调用序列；"
        "不比较事件顺序、节点名和时间戳。",
        "",
        "| 场景 | 说明 | 脚本状态 | 图状态 | 脚本错误 | 图错误 | 等价 | 差异 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['description']} | "
            f"{row['left_status']} | {row['right_status']} | "
            f"{row['left_error']} | {row['right_error']} | "
            f"{'✅' if row['equivalent'] else '❌'} | {row['differences']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_env(path: Path, env: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(env, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
