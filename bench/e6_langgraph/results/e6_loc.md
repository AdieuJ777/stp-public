# E6 代码度量

| 实现 | 类别 | 文件 | LOC | 类 | 函数 | raise | except | 控制流调用 | 恢复相关行 | Schema 相关行 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| STP script T1 | implementation | stp_t1_script.py | 82 | 0 | 4 | 0 | 0 | 6 | 0 | 4 |
| STP graph T1 | implementation | stp_t1_graph.py | 70 | 0 | 2 | 0 | 0 | 15 | 0 | 0 |
| LangGraph T1 | implementation | langgraph_t1.py | 62 | 1 | 8 | 0 | 0 | 11 | 0 | 2 |
| STP T2 | implementation | stp_t2.py | 56 | 0 | 3 | 0 | 0 | 2 | 5 | 4 |
| LangGraph T2 | implementation | langgraph_t2.py | 40 | 1 | 2 | 0 | 1 | 4 | 11 | 2 |
| Shared business logic | shared | shared.py | 42 | 0 | 6 | 1 | 0 | 0 | 0 | 0 |

> LOC 定义为“非空、非纯注释行”，docstring 计入；用于相对比较。
> 业务函数放在 `shared.py`，不计入各实现的编排 LOC。
> 这些是启发式结构指标，不能替代人工代码审查。
