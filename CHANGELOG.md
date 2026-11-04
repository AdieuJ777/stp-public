# Changelog

## [0.2.0] - 2026-11-03

### Added

- 结构化的 `TaskSpec` / `TaskRun` / `ExecutionEvent` / `ToolSpec` 契约；
- 脚本 API 与图 API 生成同一份 `TaskIR`，由同一个 `TaskRuntime` 执行；
- 静态计划校验、输入/输出 Schema 校验、统一错误码；
- 步骤级重试、退避、超时、步数与模型调用限制、取消；
- 事件日志（内存 / JSONL / SQLite）、`compare_runs` 双模式差分；
- JSON 检查点、失败现场保存、按 run 或 checkpoint_id 恢复；
- 恢复时跳过已完成节点、校验分支路由与路径一致性；
- 跨重试/恢复稳定的幂等键（`lineage_id:node_id:step_index`）；
- 检查点默认 AES-GCM 加密，支持旧明文检查点读取；
- 同步 callable 线程池执行，避免阻塞事件循环；
- `events_for(run_id)` 按 run 隔离事件；
- 记账 Agent Demo 与最小性能基准；
- 双模式 parity 测试：嵌套分支、工具失败/重试、LLM、空 case、取消、单层/嵌套分支恢复、步骤/任务超时、模型调用/步数限制；图模式多 end、共享节点、多 end 恢复。

### Changed

- 包名、CLI、环境变量与文档统一为 `stp`；
- 版本从 `0.1.0` 对齐到 `0.2.0`。

### Known limitations

- 暂不支持循环、并行、子图、分布式调度；
- 暂停/人工审批恢复、执行回放尚未实现；
- `plan_hash` / `spec_hash` 是结构哈希，不覆盖函数体变化；
- 检查点密钥来自环境变量或本地 key 文件，不等同于 KMS。
