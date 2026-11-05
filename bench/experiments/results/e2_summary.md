# E2 恢复有效性实验结果

- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：6a4256a91d1d36df2c809d383fe49930cf55bdb0

## 总体指标

- 安全恢复成功率：4/4（100.0%）
- 恢复输出一致性：100.0%
- 已完成步骤重复执行率：0/5（0.0%）
- 安全场景恢复耗时中位数：5.30 ms
- 危险场景正确拒绝率：5/5（100.0%）

## 安全恢复

| 场景 | 说明 | 首次状态 | 恢复状态 | 输出一致 | 已完成步骤 | 重复执行 | 恢复 ms |
|---|---|---|---|---|---:|---:|---:|
| linear_mid | 5 步线性任务，第 3 步首次失败 | FAILED | SUCCEEDED | ✅ | 2 | 0 | 9.82 |
| branch_high | 单层分支 high 步骤首次失败 | FAILED | SUCCEEDED | ✅ | 1 | 0 | 5.23 |
| nested_high_eu | 嵌套分支 high_eu 步骤首次失败 | FAILED | SUCCEEDED | ✅ | 1 | 0 | 5.37 |
| tool_first_failure | 工具步骤首次失败，恢复后成功 | FAILED | SUCCEEDED | ✅ | 1 | 0 | 4.38 |

## 危险场景

| 场景 | 说明 | 期望错误码 | 实际结果 | 实际错误码 | 正确 | 恢复 ms |
|---|---|---|---|---|---|---:|
| non_idempotent_unknown | 非幂等副作用工具失败，应拒绝自动恢复 | E_RESUME_UNSUPPORTED | refused | E_RESUME_UNSUPPORTED | ✅ | 7.14 |
| corrupt_checkpoint | 最新检查点被破坏，应拒绝恢复 | E_CHECKPOINT_INVALID | refused | E_CHECKPOINT_INVALID | ✅ | 1.58 |
| changed_plan | 恢复时计划结构变化，应拒绝 | E_CHECKPOINT_INCOMPATIBLE | refused | E_CHECKPOINT_INCOMPATIBLE | ✅ | 7.70 |
| changed_input | 恢复时输入变化，应拒绝 | E_CHECKPOINT_INCOMPATIBLE | refused | E_CHECKPOINT_INCOMPATIBLE | ✅ | 9.00 |
| route_mismatch | 恢复时分支条件给出不同路由，应拒绝 | E_RESUME_UNSUPPORTED | failed | E_RESUME_UNSUPPORTED | ✅ | 9.71 |

> “已完成步骤重复执行率”只统计 step 节点；branch 节点按设计会在恢复时重新求值，不计入重复执行。
> 安全恢复成功要求：恢复后 SUCCEEDED、输出与不中断参考一致、已完成步骤重复执行数为 0。
> 危险场景包括：非幂等副作用 UNKNOWN、损坏检查点、计划变化、输入变化、分支路由变化。
> `route_mismatch` 在运行期检测到路由变化，因此表现为 `FAILED` + `E_RESUME_UNSUPPORTED`；其余危险场景在恢复预检查阶段被拒绝。
> 数字用于报告量级，不是性能承诺。
