# E6：STP vs LangGraph 对照

这是一次**外部基线对照**，不是“谁更好”的功能竞赛。

## 安装

LangGraph 是可选依赖，不进入 STP 核心依赖：

```bash
cd stp
uv sync --extra dev --extra e6
```

固定版本见 `requirements-e6.txt`：
- `langgraph==1.2.20`
- `langgraph-checkpoint-sqlite==3.1.3`

## 运行

```bash
uv run python bench/e6_langgraph/run_compare.py
uv run python bench/e6_langgraph/measure.py
```

输出：

```text
bench/e6_langgraph/results/
├── e6_comparison.csv
├── e6_summary.md
├── e6_loc.csv
├── e6_loc.md
└── env.json
```

## 场景

- **T1**：审批/记账正常路径；
  - STP 脚本、STP 图、LangGraph 三种实现比较输出；
- **T2**：第 3 步首次失败后恢复；
  - STP 与 LangGraph 比较恢复输出和已完成步骤重复执行数。

业务函数在 `shared.py`，两套实现共用，不计入编排 LOC。

## 指标

- `e6_comparison.csv`：任务、实现、首次/恢复状态、输出是否符合预期、重复已完成步骤、调用计数；
- `e6_loc.csv`：LOC、类/函数、raise/except、控制流调用、恢复相关行、Schema 相关行；
- `e6_summary.md`：对照结论；
- `e6_loc.md`：代码度量表。

## 结果（样例）

| 任务 | 实现 | 首次状态 | 恢复状态 | 输出符合预期 | 重复已完成步骤 | LOC |
|---|---|---|---|---|---:|---:|
| T1 | STP 脚本 | SUCCEEDED | — | ✅ | — | 82 |
| T1 | STP 图 | SUCCEEDED | — | ✅ | — | 70 |
| T1 | LangGraph | SUCCEEDED | — | ✅ | — | 62 |
| T2 | STP | FAILED | SUCCEEDED | ✅ | 0 | 56 |
| T2 | LangGraph | FAILED | SUCCEEDED | ✅ | 0 | 40 |

- 五种实现的可观察输出都与预期一致；
- T2 两边都恢复成功，`s1`、`s2` 没有重复执行；
- 在这个**小任务、单开发者**的对照里，LangGraph 的编排 LOC 更少；
- STP 的额外 LOC 主要来自 `TaskSpec` / 输出 Schema / 显式检查点与恢复调用；
- 这不构成“STP 普遍优于/劣于 LangGraph”的结论，只是范围明确的 trade-off。

## 边界

- 单开发者、小任务；
- 只比较线性/分支 + 检查点恢复；
- 不比较循环、并行、多 Agent、流式、生态；
- 不声称 STP 普遍优于 LangGraph；
- 数字用于报告量级，不是性能承诺。
