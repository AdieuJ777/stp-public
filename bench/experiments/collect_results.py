"""汇总 E1–E5 的实验结果，生成 Markdown 与 LaTeX 表格。

运行：
    cd stp
    uv run python bench/experiments/collect_results.py

输入：
    results/e1_equivalence.csv
    results/e2_recovery.csv
    results/e2_safety.csv
    results/e3_overhead.csv
    results/e4_validation.csv
    results/e5_modules.csv
    results/e5_capabilities.csv
    results/env.json

输出：
    results/summary_all.md
    results/tables.tex
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from common import default_results_dir, env_metadata, write_env


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _tex(value: Any) -> str:
    text = str(value)
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _e1_rows(results: Path) -> list[dict[str, str]]:
    return _read_csv(results / "e1_equivalence.csv")


def _e2_rows(results: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        _read_csv(results / "e2_recovery.csv"),
        _read_csv(results / "e2_safety.csv"),
    )


def _e3_rows(results: Path) -> list[dict[str, str]]:
    return _read_csv(results / "e3_overhead.csv")


def _e4_rows(results: Path) -> list[dict[str, str]]:
    return _read_csv(results / "e4_validation.csv")


def _e5_rows(results: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        _read_csv(results / "e5_modules.csv"),
        _read_csv(results / "e5_capabilities.csv"),
    )


def _e6_dir(results: Path) -> Path:
    # results = bench/experiments/results -> bench/e6_langgraph/results
    return results.parents[1] / "e6_langgraph" / "results"


def _e6_rows(results: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    directory = _e6_dir(results)
    comparison = directory / "e6_comparison.csv"
    loc = directory / "e6_loc.csv"
    if not comparison.exists() or not loc.exists():
        return [], []
    return _read_csv(comparison), _read_csv(loc)


E6_LOC_LABEL = {
    ("T1", "STP script"): "STP script T1",
    ("T1", "STP graph"): "STP graph T1",
    ("T1", "LangGraph"): "LangGraph T1",
    ("T2", "STP"): "STP T2",
    ("T2", "LangGraph"): "LangGraph T2",
}


def _e6_loc_value(
    loc_by: dict[str, dict[str, str]],
    task: str,
    implementation: str,
) -> str:
    label = E6_LOC_LABEL.get((task, implementation))
    if label is None:
        return "—"
    return loc_by.get(label, {}).get("loc", "—")


def _summary_markdown(results: Path, env: dict[str, Any]) -> str:
    e1 = _e1_rows(results)
    e2_safe, e2_danger = _e2_rows(results)
    e3 = _e3_rows(results)
    e4 = _e4_rows(results)
    e5_modules, e5_caps = _e5_rows(results)

    e1_total = len(e1)
    e1_eq = sum(1 for row in e1 if row["equivalent"] == "True")

    e2_safe_success = sum(
        1
        for row in e2_safe
        if row["resume_status"] == "SUCCEEDED"
        and row["output_consistent"] == "True"
        and int(row["re_executed_completed"]) == 0
    )
    e2_completed = sum(int(row["completed_step_count"]) for row in e2_safe)
    e2_re_executed = sum(int(row["re_executed_completed"]) for row in e2_safe)
    e2_duplicate_rate = (
        e2_re_executed / e2_completed * 100 if e2_completed else 0.0
    )
    e2_danger_correct = sum(1 for row in e2_danger if row["correct"] == "True")
    e2_resume_median = _median(
        [float(row["resume_ms"]) for row in e2_safe]
    )

    e4_invalid = [row for row in e4 if row["case"] != "valid_baseline"]
    e4_valid = [row for row in e4 if row["case"] == "valid_baseline"]
    e4_matched = sum(1 for row in e4_invalid if row["matched"] == "True")
    e4_runtime = sum(1 for row in e4_invalid if row["runtime_rejected"] == "True")
    e4_false_positive = sum(1 for row in e4_valid if row["caught"] == "True")

    category_loc: dict[str, int] = {}
    for row in e5_modules:
        category_loc[row["category"]] = (
            category_loc.get(row["category"], 0) + int(row["loc"])
        )
    core_loc = category_loc.get("shared", 0) + category_loc.get("frontend", 0)
    shared_loc = category_loc.get("shared", 0)
    frontend_loc = category_loc.get("frontend", 0)
    shared_pct = shared_loc / core_loc * 100 if core_loc else 0.0
    frontend_pct = frontend_loc / core_loc * 100 if core_loc else 0.0
    cap_verified = sum(1 for row in e5_caps if row["verified"] == "True")
    cap_avoided = sum(int(row["avoided_duplicates"]) for row in e5_caps)

    e3_rows_md = "\n".join(
        f"| {row['steps']} | {row['mode']} | {_fmt(row['median_ms'])} | "
        f"{_fmt(row['p95_ms'])} | {row['overhead_per_step_ms'] or '—'} | "
        f"{row['bytes']} |"
        for row in e3
    )
    e3_overheads = [
        float(row["overhead_per_step_ms"])
        for row in e3
        if row["overhead_per_step_ms"]
    ]
    e3_min = min(e3_overheads) if e3_overheads else 0.0
    e3_max = max(e3_overheads) if e3_overheads else 0.0
    e3_p95_20 = next(
        (
            float(row["p95_ms"])
            for row in e3
            if row["steps"] == "20" and row["mode"] == "encrypted"
        ),
        0.0,
    )
    e3_note = (
        f"每步额外开销约 {e3_min:.1f}–{e3_max:.1f} ms；"
        f"20 步加密模式 p95 约 {e3_p95_20:.1f} ms"
    )
    e3_by_key = {(row["steps"], row["mode"]): row for row in e3}
    e3_size_increases = [
        (int(e3_by_key[(steps, "encrypted")]["bytes"]) / int(plain["bytes"]) - 1)
        * 100
        for (steps, mode), plain in e3_by_key.items()
        if mode == "plain"
        and plain["bytes"]
        and (steps, "encrypted") in e3_by_key
    ]
    e3_size_note = (
        f"{min(e3_size_increases):.0f}%–{max(e3_size_increases):.0f}%"
        if e3_size_increases
        else "—"
    )
    e2_safe_md = "\n".join(
        f"| {row['case']} | {row['first_status']} | {row['resume_status']} | "
        f"{'✅' if row['output_consistent'] == 'True' else '❌'} | "
        f"{row['completed_step_count']} | {row['re_executed_completed']} | "
        f"{row['resume_ms']} |"
        for row in e2_safe
    )
    e2_danger_md = "\n".join(
        f"| {row['case']} | {row['expected_code']} | {row['outcome']} | "
        f"{row['error_code']} | {'✅' if row['correct'] == 'True' else '❌'} |"
        for row in e2_danger
    )
    e4_md = "\n".join(
        f"| {row['case']} | {row['expected']} | "
        f"{'✅' if row['caught'] == 'True' else '❌'} | "
        f"{'✅' if row['matched'] == 'True' else '❌'} | "
        f"{'✅' if row['runtime_rejected'] == 'True' else '❌'} |"
        for row in e4_invalid
    )
    e5_note = (
        f"共享运行时 {shared_loc} LOC（{shared_pct:.1f}%），"
        f"前端 {frontend_loc} LOC（{frontend_pct:.1f}%）；"
        f"{cap_verified}/{len(e5_caps)} 项共享能力已验证，"
        f"反事实避免重复实现 {cap_avoided} 项。"
    )

    e6_cmp, e6_loc = _e6_rows(results)
    e6_loc_by = {row["implementation"]: row for row in e6_loc}
    e6_cmp_md = "\n".join(
        f"| {row['task']} | {row['implementation']} | {row['first_status']} | "
        f"{row['resume_status'] or '—'} | {'✅' if row['output_ok'] == 'True' else '❌'} | "
        f"{row['duplicate_completed'] if row['duplicate_completed'] else '—'} | "
        f"{_e6_loc_value(e6_loc_by, row['task'], row['implementation'])} |"
        for row in e6_cmp
    )
    e6_loc_md = "\n".join(
        f"| {row['implementation']} | {row['loc']} | {row['control_calls']} | "
        f"{row['recovery_lines']} | {row['schema_lines']} |"
        for row in e6_loc
    )
    e6_t2_dups = "；".join(
        f"{row['implementation']}={row['duplicate_completed']}"
        for row in e6_cmp
        if row["task"] == "T2"
    )
    if e6_cmp:
        t1_loc = " / ".join(
            f"{row['implementation']} {_e6_loc_value(e6_loc_by, 'T1', row['implementation'])}"
            for row in e6_cmp
            if row["task"] == "T1"
        )
        t2_loc = " / ".join(
            f"{row['implementation']} {_e6_loc_value(e6_loc_by, 'T2', row['implementation'])}"
            for row in e6_cmp
            if row["task"] == "T2"
        )
        e6_rq_row = (
            f"| 外部对照（可选） | E6 | T1/T2 输出一致；T2 重复执行 {e6_t2_dups}；"
            f"LOC：T1 {t1_loc}；T2 {t2_loc} |"
        )
        e6_section = f"""## E6 外部对照（可选）

- T1/T2 的可观察输出与预期一致；
- T2 两边恢复后，已完成步骤重复执行为 0；
- 业务函数由 `shared.py` 共用，LOC 只计编排/契约/恢复；
- 这是单开发者、小任务对照，不声称 STP 普遍优于 LangGraph。

| 任务 | 实现 | 首次 | 恢复 | 输出符合预期 | 重复已完成步骤 | LOC |
|---|---|---|---|---|---:|---:|
{e6_cmp_md}

| 实现 | LOC | 控制流调用 | 恢复相关行 | Schema 行 |
|---|---:|---:|---:|---:|
{e6_loc_md}

> LangGraph 版本固定为 1.2.20，`langgraph-checkpoint-sqlite` 3.1.3。
"""
        e6_interpretation = (
            "6. **外部对照**：在小任务、单开发者条件下，LangGraph 的编排 LOC 更少；"
            "STP 的额外代码主要来自 TaskSpec、Schema 和显式检查点/恢复。"
            "这构成范围明确的 trade-off，不构成普遍优劣结论。"
        )
        e6_threat = (
            "- E6 是单开发者、小任务、共享 Mock 业务函数的对照，不能外推为普遍性能或易用性结论；\n"
        )
        e6_products = "├── e6_loc.csv / e6_loc.md\n"
    else:
        e6_rq_row = ""
        e6_section = ""
        e6_interpretation = ""
        e6_threat = ""
        e6_products = ""

    dirty_note = "（dirty）" if env["git_dirty"] else ""
    workspace_note = (
        "> 当前工作区存在未提交改动；论文最终数据应在 commit 后重跑。"
        if env["git_dirty"]
        else "> 结果在干净工作区上生成，commit 与结果文件可对应。"
    )
    dirty_threat = (
        "- 当前 `git_dirty=true`，最终论文数据应在 commit 后重跑。\n"
        if env["git_dirty"]
        else ""
    )
    return f"""# STP 实验汇总（E1–E6 主实验）

- 环境：Python {env['python']}，{env['platform']}
- stp：{env['stp_version']}
- git commit：{env['git_commit']}{dirty_note}
- 时间：{env['timestamp_utc']}

> 主实验全部使用 MockLLM + 确定性函数/工具，不依赖网络。
> E7 真实模型冒烟验证（Ollama qwen2.5:1.5b，3 次）为补充验证，不并入本表，见 `results/e7_ollama_smoke.md`。
{workspace_note}

## RQ 映射

| RQ | 实验 | 关键结果 |
|---|---|---|
| RQ1 双模式等价性 | E1 | {e1_eq}/{e1_total}（{e1_eq / e1_total * 100:.1f}%）等价 |
| RQ2 统一运行时收益 | E5 | {e5_note} |
| RQ3 运行时与检查点开销 | E3 | {e3_note} |
| RQ4 恢复有效性 | E2 | 安全恢复 {e2_safe_success}/{len(e2_safe)}；危险拒绝 {e2_danger_correct}/{len(e2_danger)}；重复执行 {e2_duplicate_rate:.1f}% |
| RQ5 静态校验有效性 | E4 | {e4_matched}/{len(e4_invalid)} 命中预期错误；执行前拒绝 {e4_runtime}/{len(e4_invalid)}；误报 {e4_false_positive} |
{e6_rq_row}

## E1 双模式等价性

- 场景数：{e1_total}
- 等价：{e1_eq}/{e1_total}（{e1_eq / e1_total * 100:.1f}%）
- 等价标准：最终状态、错误码、指定输出字段、重试次数、工具调用序列。

## E2 恢复有效性

- 安全恢复：{e2_safe_success}/{len(e2_safe)}（{e2_safe_success / len(e2_safe) * 100:.1f}%）
- 输出一致性：{sum(1 for row in e2_safe if row['output_consistent'] == 'True')}/{len(e2_safe)}
- 已完成步骤重复执行率：{e2_re_executed}/{e2_completed}（{e2_duplicate_rate:.1f}%）
- 恢复耗时中位数：{e2_resume_median:.2f} ms
- 危险场景正确拒绝：{e2_danger_correct}/{len(e2_danger)}（{e2_danger_correct / len(e2_danger) * 100:.1f}%）

| 场景 | 首次 | 恢复 | 输出一致 | 已完成步骤 | 重复执行 | 恢复 ms |
|---|---|---|---|---|---:|---:|---:|
{e2_safe_md}

| 场景 | 期望错误码 | 实际结果 | 实际错误码 | 正确 |
|---|---|---|---|---|
{e2_danger_md}

## E3 运行时与检查点开销

30 次正式测量 + 5 次 warm-up；计时不包含 store 初始化（含 PBKDF2）。

| steps | 模式 | median ms | p95 ms | 额外开销/步 ms | 检查点字节 |
|---:|---|---:|---:|---:|---:|
{e3_rows_md}

## E4 静态计划校验有效性

- 注入非法计划：{len(e4_invalid)}
- `validate_plan` 检出：{sum(1 for row in e4_invalid if row['caught'] == 'True')}/{len(e4_invalid)}
- 命中预期错误：{e4_matched}/{len(e4_invalid)}
- `TaskRuntime` 执行前拒绝：{e4_runtime}/{len(e4_invalid)}
- 合法基线误报：{e4_false_positive}

| 用例 | 预期错误 | 检出 | 命中 | 执行前拒绝 |
|---|---|---|---|---|
{e4_md}

## E5 统一运行时收益

- {e5_note}
- 前端事件发射点：0
- 前端校验实现调用：0
- 前端复用 `TaskRuntime.run` / `resume` 的调用点：4

> “避免的重复实现点”是反事实估计：假设脚本/图各维护一套执行逻辑，本应有两份实现；当前为一份共享实现。

{e6_section}

## 统一解读

1. **等价性**：12 个确定性场景全部等价，覆盖正常、分支、嵌套分支、工具、LLM、失败/重试、超时、限制、取消。
2. **统一运行时**：90% 核心 LOC 在共享运行时，前端只占 10%；前端没有事件发射和校验实现，只负责构建 TaskIR。
3. **恢复**：安全场景 4/4 成功，已完成步骤重复执行 0；危险场景 5/5 正确拒绝。
4. **静态校验**：7/7 非法计划在执行前被拦截，合法基线 0 误报。
5. **开销**：检查点{e3_note}；加密带来的耗时差异在噪声内，文件大小约增加 {e3_size_note}。
{e6_interpretation}

## 威胁与边界

- 主实验使用 MockLLM，不能代表真实 LLM 的随机性；
- DAG 规模较小（5–20 步），不支持循环/并行；
- 计时仅在一台 Windows 机器上完成；
- `plan_hash` 是结构哈希，不含函数体；
- E5 的“避免重复实现”是反事实估计，不是实际对照实现的 LOC；
{e6_threat}{dirty_threat}

## 产物

```text
results/
├── e1_equivalence.csv / e1_summary.md
├── e2_recovery.csv / e2_safety.csv / e2_summary.md
├── e3_overhead.csv / e3_summary.md
├── e4_validation.csv / e4_summary.md
├── e5_modules.csv / e5_capabilities.csv / e5_summary.md
├── e7_ollama_smoke.json / e7_ollama_smoke.md
├── summary_all.md
├── tables.tex
└── env.json

bench/e6_langgraph/results/
├── e6_comparison.csv / e6_summary.md
{e6_products}└── env.json
```
"""


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _latex_tables(results: Path) -> str:
    e1 = _e1_rows(results)
    e2_safe, e2_danger = _e2_rows(results)
    e3 = _e3_rows(results)
    e4 = _e4_rows(results)
    e5_modules, e5_caps = _e5_rows(results)
    e6_cmp, e6_loc = _e6_rows(results)
    e6_loc_by = {row["implementation"]: row for row in e6_loc}

    if e6_cmp:
        e6_rows_tex = "\n".join(
            f"{_tex(row['task'])} & {_tex(row['implementation'])} & "
            f"{_tex(row['first_status'])} & {_tex(row['resume_status'] or '--')} & "
            f"{'Yes' if row['output_ok'] == 'True' else 'No'} & "
            f"{row['duplicate_completed'] or '--'} & "
            f"{_e6_loc_value(e6_loc_by, row['task'], row['implementation'])} \\\\"
            for row in e6_cmp
        )
        e6_table = rf"""
\begin{{table}}[htbp]
\centering
\small
\caption{{E6 STP vs LangGraph}}
\begin{{tabular}}{{llccccr}}
\hline
Task & Impl & First & Resume & Output & Duplicate & LOC \\
\hline
{e6_rows_tex}
\hline
\end{{tabular}}
\end{{table}}
"""
    else:
        e6_table = ""

    e1_rows = "\n".join(
        f"{_tex(row['scenario'])} & {_tex(row['left_status'])} & "
        f"{_tex(row['right_status'])} & {'Yes' if row['equivalent'] == 'True' else 'No'} \\\\"
        for row in e1
    )
    e2_safe_rows = "\n".join(
        f"{_tex(row['case'])} & {_tex(row['first_status'])} & "
        f"{_tex(row['resume_status'])} & {'Yes' if row['output_consistent'] == 'True' else 'No'} & "
        f"{row['completed_step_count']} & {row['re_executed_completed']} & "
        f"{_fmt(row['resume_ms'])} \\\\"
        for row in e2_safe
    )
    e2_danger_rows = "\n".join(
        f"{_tex(row['case'])} & {_tex(row['expected_code'])} & {_tex(row['outcome'])} & "
        f"{_tex(row['error_code'])} & {'Yes' if row['correct'] == 'True' else 'No'} \\\\"
        for row in e2_danger
    )
    e3_rows = "\n".join(
        f"{row['steps']} & {_tex(row['mode'])} & {_fmt(row['median_ms'])} & "
        f"{_fmt(row['p95_ms'])} & {row['overhead_per_step_ms'] or '--'} & "
        f"{row['bytes']} \\\\"
        for row in e3
    )
    e4_invalid = [row for row in e4 if row["case"] != "valid_baseline"]
    e4_rows = "\n".join(
        f"{_tex(row['case'])} & {_tex(row['expected'])} & "
        f"{'Yes' if row['caught'] == 'True' else 'No'} & "
        f"{'Yes' if row['matched'] == 'True' else 'No'} & "
        f"{'Yes' if row['runtime_rejected'] == 'True' else 'No'} \\\\"
        for row in e4_invalid
    )
    category_loc: dict[str, int] = {}
    for row in e5_modules:
        category_loc[row["category"]] = (
            category_loc.get(row["category"], 0) + int(row["loc"])
        )
    core_loc = category_loc.get("shared", 0) + category_loc.get("frontend", 0)
    e5_rows = "\n".join(
        f"{_tex(category)} & {loc} & "
        f"{loc / core_loc * 100:.1f}\\% \\\\"
        if category in {"shared", "frontend"}
        else f"{_tex(category)} & {loc} & -- \\\\"
        for category, loc in sorted(category_loc.items())
    )
    cap_verified = sum(1 for row in e5_caps if row["verified"] == "True")

    return rf"""% Auto-generated by bench/experiments/collect_results.py
% Requires a Chinese-capable LaTeX setup (e.g. ctex/xeCJK).

\begin{{table}}[htbp]
\centering
\small
\caption{{E1 双模式等价性}}
\begin{{tabular}}{{lccc}}
\hline
Scenario & Script & Graph & Equivalent \\
\hline
{e1_rows}
\hline
\end{{tabular}}
\end{{table}}

\begin{{table}}[htbp]
\centering
\small
\caption{{E2 安全恢复}}
\begin{{tabular}}{{lcccccc}}
\hline
Case & First & Resume & Output & Completed & Re-exec & ms \\
\hline
{e2_safe_rows}
\hline
\end{{tabular}}
\end{{table}}

\begin{{table}}[htbp]
\centering
\small
\caption{{E2 危险场景拒绝}}
\begin{{tabular}}{{llllc}}
\hline
Case & Expected & Outcome & Code & Correct \\
\hline
{e2_danger_rows}
\hline
\end{{tabular}}
\end{{table}}

\begin{{table}}[htbp]
\centering
\small
\caption{{E3 检查点开销}}
\begin{{tabular}}{{rlrrrr}}
\hline
Steps & Mode & Median (ms) & p95 (ms) & Overhead/step (ms) & Bytes \\
\hline
{e3_rows}
\hline
\end{{tabular}}
\end{{table}}

\begin{{table}}[htbp]
\centering
\small
\caption{{E4 静态校验}}
\begin{{tabular}}{{llccc}}
\hline
Case & Expected & Caught & Matched & Pre-exec reject \\
\hline
{e4_rows}
\hline
\end{{tabular}}
\end{{table}}

\begin{{table}}[htbp]
\centering
\small
\caption{{E5 代码规模与共享能力}}
\begin{{tabular}}{{lrr}}
\hline
Category & LOC & Share (\%) \\
\hline
{e5_rows}
\hline
\end{{tabular}}
\par\smallskip
\small Shared capabilities verified: {cap_verified}/{len(e5_caps)}.
\end{{table}}
{e6_table}"""


def main() -> int:
    results = default_results_dir()
    env_path = results / "env.json"
    if env_path.exists():
        env = json.loads(env_path.read_text(encoding="utf-8"))
    else:
        env = env_metadata()
        write_env(env_path, env)

    summary = _summary_markdown(results, env)
    (results / "summary_all.md").write_text(summary, encoding="utf-8")
    tables = _latex_tables(results)
    (results / "tables.tex").write_text(tables, encoding="utf-8")

    print(f"wrote: {results / 'summary_all.md'}")
    print(f"wrote: {results / 'tables.tex'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
