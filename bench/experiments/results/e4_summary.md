# E4 静态计划校验有效性

- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：7deb2aa03f32be81f2621caa24cda570c6603349（dirty）

- 注入非法计划：7
- validate_plan 检出：7/7（100.0%）
- 命中预期错误：7/7（100.0%）
- TaskRuntime 执行前拒绝：7/7（100.0%）
- 合法基线误报：0

| 用例 | 说明 | 预期错误 | 检出 | 命中 | 错误码 | 运行时拒绝 | 错误列表 |
|---|---|---|---|---|---|---|---|
| dangling_reference | 步骤引用了不存在的 state key | unknown state key | ✅ | ✅ | E_PLAN_INVALID | ✅ | step 'bad_1' references unknown state key 'missing' |
| cycle | 计划存在环 | cycle | ✅ | ✅ | E_PLAN_INVALID | ✅ | node 'end_3' is unreachable from entry | plan contains a cycle; the prototype supports DAGs only |
| unreachable | 存在从 entry 不可达的节点 | unreachable | ✅ | ✅ | E_PLAN_INVALID | ✅ | node 'orphan_3' is unreachable from entry |
| branch_without_default | 分支 default 不在 routes 中 | default_route | ✅ | ✅ | E_PLAN_INVALID | ✅ | branch 'route_2' default_route 'missing' is not in routes |
| duplicate_output_key | 同一路径上重复 output_key | duplicate output_key | ✅ | ✅ | E_PLAN_INVALID | ✅ | duplicate output_key 'dup' in 'a_1' and 'b_2' on paths that can execute together |
| end_missing_required | end 输出映射缺少必填字段 | missing required fields | ✅ | ✅ | E_PLAN_INVALID | ✅ | end 'end_2' output_mapping is missing required fields: ['value'] |
| max_steps_too_small | max_steps 小于最短路径步数 | max_steps | ✅ | ✅ | E_PLAN_INVALID | ✅ | max_steps=1 is smaller than the shortest path (2 steps) |

- 合法基线：`validate_plan` 错误 = 无；误报 0 次。

> “运行时拒绝”表示 `TaskRuntime.run()` 在创建 TaskRun 之前抛出 `PlanValidationError`，即错误不会进入执行阶段。
> 本实验只覆盖静态结构错误；业务规则验证器失败属于运行期 `E_BUSINESS_RULE`，不计入拦截率。
