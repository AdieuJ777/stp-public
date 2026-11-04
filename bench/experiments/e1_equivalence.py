"""E1：脚本 API 与图 API 的双模式等价性实验。

运行：
    cd stp
    uv run python bench/experiments/e1_equivalence.py

输出：
    bench/experiments/results/e1_equivalence.csv
    bench/experiments/results/e1_summary.md
    bench/experiments/results/env.json
"""
from __future__ import annotations

from common import (
    default_results_dir,
    env_metadata,
    run_scenario,
    write_csv,
    write_env,
    write_markdown,
)
from scenarios import SCENARIOS


def main() -> int:
    results_dir = default_results_dir()
    env = env_metadata()
    rows = []

    print(f"E1 dual-mode equivalence: {len(SCENARIOS)} scenarios")
    for scenario in SCENARIOS:
        row = run_scenario(scenario)
        rows.append(row)
        mark = "OK  " if row["equivalent"] else "DIFF"
        print(
            f"  [{mark}] {scenario.name:<20} "
            f"script={row['left_status']:<9} graph={row['right_status']:<9}"
        )
        if not row["equivalent"]:
            print(f"         {row['differences']}")

    write_csv(results_dir / "e1_equivalence.csv", rows)
    write_markdown(results_dir / "e1_summary.md", rows, env)
    write_env(results_dir / "env.json", env)

    total = len(rows)
    equivalent = sum(1 for row in rows if row["equivalent"])
    rate = (equivalent / total * 100) if total else 0.0
    print()
    print(f"equivalent: {equivalent}/{total} ({rate:.1f}%)")
    print(f"results:    {results_dir}")
    return 0 if equivalent == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
