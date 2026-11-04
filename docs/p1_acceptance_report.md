# P1 验收报告（当前基线）

- 测试命令：`python -m pytest -q`
- 测试结果：**112 passed**
- 测试环境：Windows 11、Python 3.11、本地 `.venv`
- 需求基线：《需求规格说明书_p1阶段.docx》（内部基线，未随仓库发布）
- 追溯文档：`docs/p1_traceability.md`

## 1. 验收用例结果

| 用例 | 结果 | 证据 |
|---|---|---|
| AT-001 正常任务 | 通过 | 原有正常执行测试 |
| AT-002 工具异常定位 | 通过 | 工具错误/事件测试 |
| AT-003 上下文导出/导入 | 通过 | `test_snapshot_roundtrip_and_non_json_state` |
| AT-004 检查点保存/读取 | 通过 | `test_json_checkpoint_store_roundtrip_and_latest` |
| AT-005 前两步成功、第三步失败后恢复 | 通过 | `test_at005_first_two_succeed_third_fails_then_resume` |
| AT-006 损坏检查点 | 通过 | `test_corrupted_checkpoint_is_rejected` |
| AT-007 不兼容任务版本 | 通过 | `test_resume_rejects_changed_plan`、`test_resume_rejects_changed_spec` |
| AT-008 检查点保存失败 | 通过 | `test_checkpoint_save_failure_does_not_retry_step` |
| AT-009 恢复后输出 Schema | 通过 | 恢复任务统一经过 `_execute_end` 校验 |
| AT-010 两种 API 等价 | 通过 | `test_script_and_graph_resume_are_equivalent` |
| AT-011 日志不含敏感凭据 | 通过 | 事件脱敏覆盖步骤输出、工具输入/输出；检查点默认加密落盘 |
| AT-012 不确定副作用不重复 | 通过 | `test_non_idempotent_unknown_result_blocks_resume` |

补充验证：

- `test_random_dag_resume_preserves_calls_and_output`：随机 DAG 恢复后输出一致，已完成步骤不重复执行；
- `test_snapshot_path_consistency_rejects_unselected_branch`：混合互斥分支的检查点会被拒绝；
- `test_resume_respects_total_timeout_budget`：恢复后使用剩余执行时间预算；
- `test_task_completed_sink_failure_does_not_leave_succeeded_status`、`test_event_sink_failure_always_marks_failed`：事件 sink 失败不会假成功；
- `test_checkpoint_step_results_and_content_hash`：合法 JSON 内篡改会被内容哈希拒绝；
- `test_idempotency_key_is_stable_across_retries`、`test_idempotency_key_is_stable_across_resume`、`test_idempotency_key_is_stable_across_chained_resumes`：幂等键跨重试和恢复链稳定；
- `test_checkpoint_is_encrypted_on_disk`、`test_checkpoint_wrong_key_is_rejected`、`test_checkpoint_encrypted_file_requires_key`：检查点默认加密，磁盘不含明文秘密；
- `test_sync_function_does_not_block_event_loop`、`test_sync_function_step_timeout_is_enforced`、`test_function_tool_sync_does_not_block_event_loop`：同步 callable 不再阻塞事件循环，超时仍生效；
- `test_concurrent_runs_do_not_share_events`、`test_resume_events_are_isolated_from_original_run`：并发 run 与恢复 run 的事件流互相隔离；
- `tests/test_parity.py`：嵌套分支、工具失败/重试、LLM 步骤、空 case、取消 5 组脚本/图 parity；
- `test_branch_resume_parity`：单层分支恢复，`classify`/`route` 跳过、`high` 重跑，脚本/图输出与调用次数一致；
- `test_graph_branch_resume_route_mismatch`：图模式恢复时路由变化，返回 `E_RESUME_UNSUPPORTED`；
- `tests/test_parity_limits.py`：步骤超时、任务超时、模型调用上限、步数上限 4 组脚本/图 parity；
- `test_nested_branch_resume_parity`：外层 + 内层 branch 恢复，跳过 `classify`/`outer`/`inner`，只重跑 `high_eu`；
- `test_graph_multiple_ends_resume`：图模式多 end 恢复，只执行活跃 end；
- `tests/test_graph_only.py`：图模式多 end、共享节点/菱形 2 组回归。

## 2. 最小性能基准

运行：`python bench/p1_benchmark.py`

| steps | 无检查点中位数 ms | 有检查点中位数 ms | 额外开销 ms | 检查点字节 |
|---:|---:|---:|---:|---:|
| 5 | 2.07 | 11.67 | 9.59 | 8948 |
| 10 | 2.96 | 21.36 | 18.39 | 20760 |
| 20 | 4.70 | 42.71 | 38.01 | 53716 |

恢复测试：

| steps | 首次运行 ms | 恢复 ms | 已完成步骤重复调用 |
|---:|---:|---:|---:|
| 5 | 8.44 | 15.39 | 0 |
| 10 | 13.77 | 18.25 | 0 |
| 20 | 23.75 | 28.26 | 0 |

说明：数字只用于报告量级，不是性能承诺；检查点默认加密，字节数和耗时包含 AES-GCM 开销；
同步 `func` 步骤经线程池执行，基线也包含 offload 开销。真实模型和更大任务规模需在论文实验阶段单独测量。

## 3. 已知偏差

| 项 | 说明 |
|---|---|
| `step_results` | 由 `state` 派生，`state` 是唯一事实来源；读取时校验两者一致 |
| `RecoveryManager` | 职责集成在 `TaskRuntime`，没有独立类 |
| 恢复 run 身份 | 新 `TaskRun` + `resumed_from_run_id`，兼容 P0 终态状态机 |
| 超时预算 | 累计的是执行时间，不含进程停机时间 |
| 敏感信息脱敏 | 事件脱敏为启发式；检查点默认 AES-GCM 加密，密钥来自 `STP_CHECKPOINT_KEY` 或本地 0600 key 文件 |
| NFR-007 | 已有最小基准；完整对照实验留到论文实验阶段 |

## 4. 结论

P1 的核心功能、主验收路径、随机 DAG 恢复、路径一致性、事件失败语义、内容完整性、剩余超时预算和最小性能测量已经完成。

可以表述为：

> **P1 核心完成，112 个测试通过；剩余差异已在追溯文档中显式记录，完整性能对照实验留待论文实验阶段。**
