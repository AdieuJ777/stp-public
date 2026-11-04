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
- 真实 Ollama 只作为补充 smoke test，不进入主表。

## 待实现

- E4 静态校验有效性：注入非法计划，统计 `validate_plan` 拦截率；
- E5 统一运行时收益：代码度量与重复实现点；
- E6 LangGraph 对照（可选）。
