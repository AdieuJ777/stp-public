# 实验脚本

这些脚本用于毕业论文实验，不是 pytest 测试。

## 已实现

### E1 双模式等价性

```bash
cd stp
uv run python bench/experiments/e1_equivalence.py
```

- 12 个确定性场景：线性、单层/嵌套分支、空 case、工具成功/失败、LLM、步骤/任务超时、模型/步数限制、取消；
- 每个场景分别用脚本和图执行，`compare_runs` 比较：最终状态、错误码、指定输出字段、重试次数、工具调用序列；
- 输出：
  - `results/e1_equivalence.csv`
  - `results/e1_summary.md`
  - `results/env.json`

### E2 恢复有效性

```bash
cd stp
uv run python bench/experiments/e2_recovery.py
```

- 安全恢复：线性 / 单层分支 / 嵌套分支 / 工具步骤首次失败 → 恢复；
- 危险场景：非幂等副作用 `UNKNOWN`、损坏检查点、计划变化、输入变化、分支路由变化；
- 指标：恢复成功率、输出一致性、已完成步骤重复执行率、恢复耗时、危险场景正确拒绝率；
- 输出：
  - `results/e2_recovery.csv`
  - `results/e2_safety.csv`
  - `results/e2_summary.md`
  - `results/env.json`

> 重复执行率只统计 step 节点；branch 节点按设计会在恢复时重新求值，不计入重复执行。
> E2 的 spec 使用 `max_attempts=1`，避免首次失败被同 run 自动重试吸收；这里关注的是跨 run 恢复，不是步骤重试。

### E3 运行时与检查点开销

```bash
cd stp
uv run python bench/experiments/e3_overhead.py
```

- 5 / 10 / 20 步线性任务；
- 三种模式：无检查点、明文检查点、加密检查点；
- 30 次正式测量 + 5 次 warm-up；
- 输出：
  - `results/e3_overhead.csv`
  - `results/e3_summary.md`
  - `results/env.json`

## 约定

- 主实验全部使用 `MockLLM` + 确定性函数/工具，不依赖网络；
- 计时类实验报告 median / p95，不报单次；
- 每次运行都会记录 Python、平台、stp 版本、git commit、是否 dirty；
- 数字用于报告量级，不是性能承诺；
- 真实 Ollama 只作为补充 smoke test，不进入主表；
- `results/` 中的结果是 preliminary：`env.json` 记录 `git_dirty=true`，最终论文数据应在 commit 后重跑。

### E4 静态计划校验有效性

```bash
cd stp
uv run python bench/experiments/e4_validation.py
```

- 注入 7 类非法计划：悬空引用、成环、不可达、分支缺默认、`output_key` 冲突、`end` 缺必填字段、`max_steps` 过小；
- 统计 `validate_plan` 检出率、预期错误命中率、`TaskRuntime.run` 执行前拒绝率；
- 另跑一个合法基线，检查误报；
- 输出：
  - `results/e4_validation.csv`
  - `results/e4_summary.md`
  - `results/env.json`

### E5 统一运行时收益

```bash
cd stp
uv run python bench/experiments/e5_runtime_benefit.py
```

- 用 AST 统计核心模块 LOC、类/函数、EventType 引用、校验调用、runtime 调用；
- 统计共享运行时 / 前端 / 适配层代码比例；
- 审计 13 项共享能力（计划、遍历、契约、重试、限制、取消、事件、检查点、恢复、错误分类、差分等）是否只有一份实现；
- 按“脚本/图各写一套运行时”的反事实估计避免的重复实现点；
- 输出：
  - `results/e5_modules.csv`
  - `results/e5_capabilities.csv`
  - `results/e5_summary.md`
  - `results/env.json`

> E5 是结构化的代码度量，不是运行时实验；不比较 LangGraph，也不声称普遍更优。

### E1–E6 汇总

```bash
cd stp
uv run python bench/experiments/collect_results.py
```

- 读取 E1–E5 以及 `bench/e6_langgraph/results/` 的 CSV，生成：
  - `results/summary_all.md`：RQ 映射、关键指标、统一解读、威胁与边界；
  - `results/tables.tex`：论文用的 LaTeX 表格（需要 ctex/xeCJK）。
- E6 LangGraph 对照的代码与说明见 [`../e6_langgraph/README.md`](../e6_langgraph/README.md)。
