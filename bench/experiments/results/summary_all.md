# STP 实验汇总（E1–E5）

- 环境：Python 3.11.16，Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：7deb2aa03f32be81f2621caa24cda570c6603349（dirty）
- 时间：2026-11-05T04:24:42.371874+00:00

> 主实验全部使用 MockLLM + 确定性函数/工具，不依赖网络。
> 当前 commit/dirty 状态说明结果是在未提交工作区上生成的；论文最终数据应在 commit 后重跑。

## RQ 映射

| RQ | 实验 | 关键结果 |
|---|---|---|
| RQ1 双模式等价性 | E1 | 12/12（100.0%）等价 |
| RQ2 统一运行时收益 | E5 | 共享运行时 2994 LOC（90.0%），前端 334 LOC（10.0%）；13/13 项共享能力已验证，反事实避免重复实现 13 项。 |
| RQ3 运行时与检查点开销 | E3 | 每步额外开销约 1.6–2.2 ms；20 步加密模式 p95 约 53 ms |
| RQ4 恢复有效性 | E2 | 安全恢复 4/4；危险拒绝 5/5；重复执行 0.0% |
| RQ5 静态校验有效性 | E4 | 7/7 命中预期错误；执行前拒绝 7/7；误报 0 |

## E1 双模式等价性

- 场景数：12
- 等价：12/12（100.0%）
- 等价标准：最终状态、错误码、指定输出字段、重试次数、工具调用序列。

## E2 恢复有效性

- 安全恢复：4/4（100.0%）
- 输出一致性：4/4
- 已完成步骤重复执行率：0/5（0.0%）
- 恢复耗时中位数：5.62 ms
- 危险场景正确拒绝：5/5（100.0%）

| 场景 | 首次 | 恢复 | 输出一致 | 已完成步骤 | 重复执行 | 恢复 ms |
|---|---|---|---|---|---:|---:|---:|
| linear_mid | FAILED | SUCCEEDED | ✅ | 2 | 0 | 9.69 |
| branch_high | FAILED | SUCCEEDED | ✅ | 1 | 0 | 5.45 |
| nested_high_eu | FAILED | SUCCEEDED | ✅ | 1 | 0 | 5.79 |
| tool_first_failure | FAILED | SUCCEEDED | ✅ | 1 | 0 | 4.58 |

| 场景 | 期望错误码 | 实际结果 | 实际错误码 | 正确 |
|---|---|---|---|---|
| non_idempotent_unknown | E_RESUME_UNSUPPORTED | refused | E_RESUME_UNSUPPORTED | ✅ |
| corrupt_checkpoint | E_CHECKPOINT_INVALID | refused | E_CHECKPOINT_INVALID | ✅ |
| changed_plan | E_CHECKPOINT_INCOMPATIBLE | refused | E_CHECKPOINT_INCOMPATIBLE | ✅ |
| changed_input | E_CHECKPOINT_INCOMPATIBLE | refused | E_CHECKPOINT_INCOMPATIBLE | ✅ |
| route_mismatch | E_RESUME_UNSUPPORTED | failed | E_RESUME_UNSUPPORTED | ✅ |

## E3 运行时与检查点开销

30 次正式测量 + 5 次 warm-up；计时不包含 store 初始化（含 PBKDF2）。

| steps | 模式 | median ms | p95 ms | 额外开销/步 ms | 检查点字节 |
|---:|---|---:|---:|---:|---:|
| 5 | none | 2.02 | 2.41 | — | 0 |
| 5 | plain | 10.24 | 11.61 | 1.64 | 6484 |
| 5 | encrypted | 10.97 | 13.39 | 1.79 | 8844 |
| 10 | none | 3.13 | 4.48 | — | 0 |
| 10 | plain | 23.05 | 25.61 | 1.99 | 15400 |
| 10 | encrypted | 24.18 | 27.16 | 2.10 | 20692 |
| 20 | none | 5.31 | 6.33 | — | 0 |
| 20 | plain | 47.37 | 50.74 | 2.10 | 40616 |
| 20 | encrypted | 48.24 | 53.14 | 2.15 | 53512 |

## E4 静态计划校验有效性

- 注入非法计划：7
- `validate_plan` 检出：7/7
- 命中预期错误：7/7
- `TaskRuntime` 执行前拒绝：7/7
- 合法基线误报：0

| 用例 | 预期错误 | 检出 | 命中 | 执行前拒绝 |
|---|---|---|---|---|
| dangling_reference | unknown state key | ✅ | ✅ | ✅ |
| cycle | cycle | ✅ | ✅ | ✅ |
| unreachable | unreachable | ✅ | ✅ | ✅ |
| branch_without_default | default_route | ✅ | ✅ | ✅ |
| duplicate_output_key | duplicate output_key | ✅ | ✅ | ✅ |
| end_missing_required | missing required fields | ✅ | ✅ | ✅ |
| max_steps_too_small | max_steps | ✅ | ✅ | ✅ |

## E5 统一运行时收益

- 共享运行时 2994 LOC（90.0%），前端 334 LOC（10.0%）；13/13 项共享能力已验证，反事实避免重复实现 13 项。
- 前端事件发射点：0
- 前端校验实现调用：0
- 前端复用 `TaskRuntime.run` / `resume` 的调用点：4

> “避免的重复实现点”是反事实估计：假设脚本/图各维护一套执行逻辑，本应有两份实现；当前为一份共享实现。

## 统一解读

1. **等价性**：12 个确定性场景全部等价，覆盖正常、分支、嵌套分支、工具、LLM、失败/重试、超时、限制、取消。
2. **统一运行时**：90% 核心 LOC 在共享运行时，前端只占 10%；前端没有事件发射和校验实现，只负责构建 TaskIR。
3. **恢复**：安全场景 4/4 成功，已完成步骤重复执行 0；危险场景 5/5 正确拒绝。
4. **静态校验**：7/7 非法计划在执行前被拦截，合法基线 0 误报。
5. **开销**：检查点每步约 1.6–2.2 ms；加密带来的耗时差异在噪声内，文件大小约增加 35%–40%。

## 威胁与边界

- 主实验使用 MockLLM，不能代表真实 LLM 的随机性；
- DAG 规模较小（5–20 步），不支持循环/并行；
- 计时仅在一台 Windows 机器上完成；
- `plan_hash` 是结构哈希，不含函数体；
- E5 的“避免重复实现”是反事实估计，不是实际对照实现的 LOC；
- 当前 `git_dirty=true`，最终论文数据应在 commit 后重跑。

## 产物

```text
results/
├── e1_equivalence.csv / e1_summary.md
├── e2_recovery.csv / e2_safety.csv / e2_summary.md
├── e3_overhead.csv / e3_summary.md
├── e4_validation.csv / e4_summary.md
├── e5_modules.csv / e5_capabilities.csv / e5_summary.md
├── summary_all.md
├── tables.tex
└── env.json
```
