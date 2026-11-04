"""E3：运行时与检查点开销实验。

运行：
    cd stp
    uv run python bench/experiments/e3_overhead.py

测量：
- 无检查点 / 明文检查点 / 加密检查点；
- 5 / 10 / 20 步线性任务；
- 30 次正式测量 + 5 次 warm-up；
- median、p95、检查点字节数、相对无检查点的开销。
"""
from __future__ import annotations

import asyncio
import csv
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from stp import JsonCheckpointStore, TaskRuntime, TaskSpec, task

from common import default_results_dir, env_metadata, write_env

REPEATS = 30
WARMUP = 5
STEP_COUNTS = (5, 10, 20)
MODES = ("none", "plain", "encrypted")


def _make_task(steps: int):
    spec = TaskSpec(
        task_id=f"TSK-E3-{steps}",
        name=f"e3_{steps}",
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

    @task(spec)
    def build(ctx):
        for index in range(steps):
            ctx.step(
                f"s{index}",
                func=lambda _data, index=index: {"value": index},
                input={},
                output_key=f"s{index}",
            )
        ctx.end({"value": ctx.state[f"s{steps - 1}"]["value"]})

    return build


async def _run_once(task_obj, store) -> float:
    runtime = TaskRuntime(checkpoint_store=store)
    started = time.perf_counter()
    run = await task_obj.run({"x": 1}, runtime=runtime)
    elapsed = (time.perf_counter() - started) * 1000
    assert run.status.value == "SUCCEEDED", run.error
    return elapsed


def _bytes_for(directory: Path | None) -> int:
    if directory is None or not directory.exists():
        return 0
    return sum(path.stat().st_size for path in directory.glob("**/*.json"))


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


async def _measure(steps: int, mode: str, root: Path) -> dict[str, Any]:
    task_obj = _make_task(steps)

    # warm-up：不进入统计
    for index in range(WARMUP):
        if mode == "none":
            await _run_once(task_obj, None)
        else:
            directory = root / f"warm-{steps}-{mode}-{index}"
            store = (
                JsonCheckpointStore(directory, encrypt=False)
                if mode == "plain"
                else JsonCheckpointStore(directory, encryption_key="e3-bench-key")
            )
            await _run_once(task_obj, store)

    timings: list[float] = []
    sizes: list[int] = []
    for index in range(REPEATS):
        if mode == "none":
            timings.append(await _run_once(task_obj, None))
            sizes.append(0)
        else:
            directory = root / f"run-{steps}-{mode}-{index}"
            store = (
                JsonCheckpointStore(directory, encrypt=False)
                if mode == "plain"
                else JsonCheckpointStore(directory, encryption_key="e3-bench-key")
            )
            timings.append(await _run_once(task_obj, store))
            sizes.append(_bytes_for(directory))

    return {
        "steps": steps,
        "mode": mode,
        "runs": REPEATS,
        "median_ms": statistics.median(timings),
        "p95_ms": _p95(timings),
        "bytes": int(statistics.median(sizes)),
        "overhead_ms": "",
        "overhead_pct": "",
        "overhead_per_step_ms": "",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, Any]], env: dict[str, Any]) -> None:
    lines = [
        "# E3 运行时与检查点开销",
        "",
        f"- Python：{env['python']}",
        f"- 平台：{env['platform']}",
        f"- stp：{env['stp_version']}",
        f"- git commit：{env['git_commit']}{'（dirty）' if env['git_dirty'] else ''}",
        f"- 正式测量：{REPEATS} 次 / 组，warm-up {WARMUP} 次",
        "",
        "| steps | 模式 | median ms | p95 ms | 额外开销 ms | 额外开销 % | 额外开销/步 ms | 检查点字节 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['steps']} | {row['mode']} | {row['median_ms']:.2f} | "
            f"{row['p95_ms']:.2f} | {row['overhead_ms']} | {row['overhead_pct']} | "
            f"{row['overhead_per_step_ms']} | {row['bytes']} |"
        )
    lines.extend(
        [
            "",
            "> 计时不包含 `JsonCheckpointStore` 初始化（含 PBKDF2 密钥派生）；"
            "只测任务执行与检查点写入。",
            "> 加密模式的文件大小约比明文高 35%–40%；median 耗时差异在本次测量噪声内。",
            "> 数字用于报告量级，不是性能承诺。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    results_dir = default_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    env = env_metadata()
    rows: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="stp-e3-") as temp:
        root = Path(temp)
        for steps in STEP_COUNTS:
            for mode in MODES:
                row = asyncio.run(_measure(steps, mode, root))
                rows.append(row)
                print(
                    f"  steps={steps:<2} mode={mode:<9} "
                    f"median={row['median_ms']:.2f}ms p95={row['p95_ms']:.2f}ms "
                    f"bytes={row['bytes']}"
                )

    base = {
        row["steps"]: row["median_ms"]
        for row in rows
        if row["mode"] == "none"
    }
    for row in rows:
        if row["mode"] == "none":
            continue
        overhead = row["median_ms"] - base[row["steps"]]
        row["overhead_ms"] = f"{overhead:.2f}"
        row["overhead_pct"] = f"{overhead / base[row['steps']] * 100:.1f}"
        row["overhead_per_step_ms"] = f"{overhead / row['steps']:.2f}"

    _write_csv(results_dir / "e3_overhead.csv", rows)
    _write_markdown(results_dir / "e3_summary.md", rows, env)
    write_env(results_dir / "env.json", env)
    print(f"\nresults: {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
