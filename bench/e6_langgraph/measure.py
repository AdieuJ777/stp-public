"""E6：实现文件的 LOC 与结构指标度量。

运行：
    cd stp
    uv run python bench/e6_langgraph/measure.py

输出：
    bench/e6_langgraph/results/e6_loc.csv
    bench/e6_langgraph/results/e6_loc.md
"""
from __future__ import annotations

import ast
import csv
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"

FILES = [
    ("STP script T1", "stp_t1_script.py", "implementation"),
    ("STP graph T1", "stp_t1_graph.py", "implementation"),
    ("LangGraph T1", "langgraph_t1.py", "implementation"),
    ("STP T2", "stp_t2.py", "implementation"),
    ("LangGraph T2", "langgraph_t2.py", "implementation"),
    ("Shared business logic", "shared.py", "shared"),
]

CONTROL_CALLS = {
    "step",
    "branch",
    "end",
    "add_step",
    "add_branch",
    "add_join",
    "add_end",
    "add_route",
    "add_edge",
    "add_node",
    "add_conditional_edges",
    "set_entry",
}

RECOVERY_TERMS = (
    "checkpoint",
    "checkpointer",
    "resume",
    "thread_id",
    "configurable",
    "SqliteSaver",
    "JsonCheckpointStore",
)

SCHEMA_TERMS = ("schema", "TaskSpec", "TypedDict")


def _loc(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(
        1
        for line in text.splitlines()
        if any(term.lower() in line.lower() for term in terms)
    )


def _analyze(path: Path, label: str, category: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in CONTROL_CALLS)
            or (isinstance(node.func, ast.Name) and node.func.id in CONTROL_CALLS)
        )
    ]
    raises = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return {
        "implementation": label,
        "category": category,
        "file": path.name,
        "loc": _loc(text),
        "classes": len(classes),
        "functions": len(functions),
        "raise_sites": len(raises),
        "except_handlers": len(handlers),
        "control_calls": len(calls),
        "recovery_lines": _count_terms(text, RECOVERY_TERMS),
        "schema_lines": _count_terms(text, SCHEMA_TERMS),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# E6 代码度量",
        "",
        "| 实现 | 类别 | 文件 | LOC | 类 | 函数 | raise | except | 控制流调用 | 恢复相关行 | Schema 相关行 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['implementation']} | {row['category']} | {row['file']} | "
            f"{row['loc']} | {row['classes']} | {row['functions']} | "
            f"{row['raise_sites']} | {row['except_handlers']} | "
            f"{row['control_calls']} | {row['recovery_lines']} | "
            f"{row['schema_lines']} |"
        )
    lines.extend(
        [
            "",
            "> LOC 定义为“非空、非纯注释行”，docstring 计入；用于相对比较。",
            "> 业务函数放在 `shared.py`，不计入各实现的编排 LOC。",
            "> 这些是启发式结构指标，不能替代人工代码审查。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = [
        _analyze(BASE / filename, label, category)
        for label, filename, category in FILES
    ]
    _write_csv(RESULTS / "e6_loc.csv", rows)
    _write_markdown(RESULTS / "e6_loc.md", rows)
    for row in rows:
        print(
            f"  {row['implementation']:<24} loc={row['loc']:<4} "
            f"control={row['control_calls']:<3} recovery={row['recovery_lines']:<3} "
            f"schema={row['schema_lines']}"
        )
    print(f"\nresults: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
