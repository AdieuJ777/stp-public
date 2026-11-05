# E5 统一运行时收益（RQ2）

- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：6a4256a91d1d36df2c809d383fe49930cf55bdb0

## 代码规模

- 核心 LOC（shared + frontend）：3328
- 共享运行时 LOC：2994（90.0%）
- 前端 LOC（script.py + graph.py）：334（10.0%）
- 适配层 LOC（llm.py + tools.py）：537
- 前端 / 共享运行时：0.11

- 前端事件发射点（EventType 引用）：0
- 前端校验实现调用（validate_plan / validate_instance）：0
- 前端复用 TaskRuntime.run / resume 的调用点：4

## 共享能力

- 共享能力：13/13 已验证（符号存在）
- 前端专属实现：0
- 按“脚本/图各写一套运行时”的反事实估计，避免的重复实现点：13

| 能力 | 类别 | 共享实现 | 已验证 | 脚本专属 | 图专属 | 实现数 | 避免重复 |
|---|---|---|---|---|---|---:|---:|
| TaskIR / 计划构建 | execution | plan.py: TaskIR, PlanBuilder | ✅ | 否 | 否 | 1 | 1 |
| 图遍历与节点调度 | execution | executor.py: TaskRuntime._execute_graph | ✅ | 否 | 否 | 1 | 1 |
| 步骤执行（工具/函数/LLM） | execution | executor.py: TaskRuntime._execute_step | ✅ | 否 | 否 | 1 | 1 |
| 静态计划校验 | contract | validation.py: validate_plan | ✅ | 否 | 否 | 1 | 1 |
| 输入/输出/工具契约校验 | contract | schema.py: validate_instance | ✅ | 否 | 否 | 1 | 1 |
| 重试与退避 | reliability | executor.py: TaskRuntime._can_retry / _schedule_retry | ✅ | 否 | 否 | 1 | 1 |
| 超时、步数与模型调用限制 | reliability | executor.py: TaskRuntime.run / _execute_step; context.py | ✅ | 否 | 否 | 1 | 1 |
| 取消传播 | reliability | tokens.py: CancellationToken; context.py | ✅ | 否 | 否 | 1 | 1 |
| 事件总线与事件存储 | observability | events.py: EventBus; stores.py | ✅ | 否 | 否 | 1 | 1 |
| 检查点与完整性校验 | recovery | checkpoint.py: Checkpoint, JsonCheckpointStore | ✅ | 否 | 否 | 1 | 1 |
| 恢复执行 | recovery | executor.py: TaskRuntime.resume / _resolve_resume_checkpoint | ✅ | 否 | 否 | 1 | 1 |
| 错误分类 | observability | errors.py: FrameworkError 及子类 | ✅ | 否 | 否 | 1 | 1 |
| 双模式可观察结果比较 | evaluation | diff.py: compare_runs | ✅ | 否 | 否 | 1 | 1 |

## 模块度量

| 模块 | 类别 | LOC | 类 | 函数/方法 | EventType 引用 | raise 点 | 校验调用 | runtime 调用 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| models.py | shared | 189 | 11 | 9 | 0 | 8 | 0 | 0 |
| plan.py | shared | 120 | 5 | 10 | 0 | 9 | 0 | 0 |
| refs.py | shared | 36 | 2 | 5 | 0 | 0 | 0 | 0 |
| errors.py | shared | 103 | 26 | 2 | 0 | 0 | 0 | 0 |
| schema.py | shared | 41 | 0 | 3 | 0 | 2 | 0 | 0 |
| validation.py | shared | 217 | 1 | 13 | 0 | 1 | 0 | 0 |
| events.py | shared | 57 | 2 | 6 | 0 | 0 | 0 | 0 |
| stores.py | shared | 101 | 3 | 14 | 0 | 0 | 0 | 0 |
| checkpoint.py | shared | 357 | 3 | 24 | 0 | 14 | 0 | 0 |
| context.py | shared | 521 | 1 | 21 | 4 | 34 | 1 | 0 |
| executor.py | shared | 1107 | 1 | 26 | 31 | 43 | 4 | 0 |
| diff.py | shared | 70 | 1 | 4 | 2 | 1 | 0 | 0 |
| redaction.py | shared | 52 | 0 | 2 | 0 | 0 | 0 | 0 |
| tokens.py | shared | 23 | 2 | 8 | 0 | 0 | 0 | 0 |
| script.py | frontend | 206 | 2 | 15 | 0 | 9 | 0 | 2 |
| graph.py | frontend | 128 | 2 | 14 | 0 | 2 | 0 | 2 |
| llm.py | adapter | 289 | 4 | 14 | 0 | 12 | 2 | 0 |
| tools.py | adapter | 248 | 5 | 12 | 5 | 11 | 2 | 0 |
| demo/accounting.py | demo | 406 | 4 | 16 | 0 | 4 | 0 | 1 |

> LOC 定义为“非空、非纯注释行”，docstring 计入；用于相对比较，不作为绝对规模指标。
> “避免的重复实现点”是反事实估计：假设脚本/图各维护一套执行逻辑，本应有两份实现；当前为一份共享实现，因此每项能力避免 1 个重复实现点。
> 本实验不比较 LangGraph 或其它框架的代码量，也不声称普遍更优。
