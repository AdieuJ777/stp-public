# E6 STP vs LangGraph 对照

- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0
- git commit：64ddd7c4ec4c68bb583c56e114d7bdee57afca23（dirty）

| 任务 | 实现 | 首次状态 | 恢复状态 | 输出符合预期 | 重复已完成步骤 | 错误 |
|---|---|---|---|---|---:|---|
| T1 | STP script | SUCCEEDED | — | ✅ | — |  |
| T1 | STP graph | SUCCEEDED | — | ✅ | — |  |
| T1 | LangGraph | SUCCEEDED | — | ✅ | — |  |
| T2 | STP | FAILED | SUCCEEDED | ✅ | 0 |  |
| T2 | LangGraph | FAILED | SUCCEEDED | ✅ | 0 |  |

> T1：审批/记账正常路径，比较三种实现的可观察输出。
> T2：第 3 步首次失败后恢复；重复已完成步骤只统计 s1、s2。
> 业务函数（抽取、预算、审批、记账）由 `shared.py` 提供，不计入各实现的编排 LOC。
> 这是一次单开发者、小任务的对照，不声称 STP 普遍优于 LangGraph。
> 代码 LOC 与结构指标见 `e6_loc.csv`（由 `measure.py` 生成）。
