# P1 需求证据链复核（2026-11-03）

> 复核基线：《需求规格说明书_p1阶段.docx》（V1.0，内部需求基线，未随仓库发布）
> 代码基线：`stp/`，112 个测试通过（`python -m pytest -o addopts="" -q` → `112 passed in 7.33s`）
> 复核方式：docx → Markdown 后逐条对照代码、测试与 `docs/p1_traceability.md` / `docs/p1_acceptance_report.md`
> 性质：只读复核，未修改任何代码。

---

## 0. 总结论

**P1 的证据链整体成立，可以支撑“P1 核心完成”的表述；但有 2 个真实缺口和若干语义偏差需要在答辩前处理或显式声明。**

> **2026-11-03 修复更新**：B-01（幂等键跨重试/恢复不稳定）与 F-01（checkpoint 明文保存 input/state）已修复。FR-008、AT-011、NFR-004 的结论由“部分”转为“满足”；F-01 条目保留为历史记录，并在原处标注修复方式。下表为修复后的统计。

| 维度 | 结论 |
|---|---|
| 29 条功能需求（FR） | 26 条满足、2 条部分满足、1 条有语义偏差 |
| 12 条验收测试（AT） | 11 条满足、1 条需限定范围 |
| 7 条非功能需求（NFR） | 5 条满足、2 条部分 |
| 11 条完成判定 | 9 条满足、2 条部分 |

**最需要处理的两条**：

1. **NFR-004 / AT-011**：事件日志做了脱敏，但 **checkpoint 曾明文保存 `input` 和 `state`**。如果真实任务输入里带 API Key 或 PII，会落盘。**已于 2026-11-03 修复：checkpoint 默认 AES-GCM 加密。**
2. **FR-023**：恢复只校验 `plan_hash` / `spec_hash` 这类**结构哈希**，函数体、验证器、分支条件实现变化检测不到。已在 README 写成限制，但需求原文是“任务定义兼容性”，需要明确“任务定义”是否包含实现。

其余偏差（`task_version` 语义、超时预算口径、`RecoveryManager` 命名、`step_results` 派生）都已在追溯文档中说明，属于可接受的“设计偏差”，不是遗漏。

---

## 1. 功能需求逐条复核

图例：✅ 满足；⚠️ 部分满足；🔁 设计偏差（已说明）。

### 4.1 执行诊断

| 编号 | 结论 | 证据 | 说明 |
|---|---|---|---|
| FR-001 | ✅ | 49 个 P0 用例通过 | 原有事件保留 |
| FR-002 | ✅ | `models.py` 的 `ExecutionEvent.task_id/step_id`；`test_events_carry_task_and_step_ids` | 任务级事件 `step_id=None` |
| FR-003 | ✅ | `ExecutionEvent.timestamp`、`duration_ms`；`test_event_diagnostics_fields` | 步骤/LLM/工具事件均有耗时 |
| FR-004 | ✅ | `attempt`、`ERROR_OCCURRED`、`RETRY_SCHEDULED`、`TASK_FAILED`；重试测试 | 能区分单次失败与最终失败 |
| FR-005 | ✅ | `errors.py` 细粒度错误码 + 追溯文档 §4 映射表 | 未维护第二套异常体系 |
| FR-006 | ✅ | `CHECKPOINT_SAVED` / `CHECKPOINT_LOADED` / `TASK_RESUMED` / `RECOVERY_FAILED` | 见下方 F-07 的边界说明 |
| FR-007 | ✅ | `test_task_completed_sink_failure...`、`test_event_sink_failure_always_marks_failed` | 先发 `TASK_COMPLETED` 再转 `SUCCEEDED` |
| FR-008 | ✅ | `redaction.py`；`test_event_payload_redacts_sensitive_fields`、`test_tool_events_redact_sensitive_input_and_output`；检查点默认 AES-GCM 加密 | 事件脱敏仍是启发式；checkpoint 由 F-01 修复覆盖 |

### 4.2 上下文快照

| 编号 | 结论 | 证据 | 说明 |
|---|---|---|---|
| FR-009 | ✅ | `ExecutionContext.export_snapshot()` | 严格 JSON 往返 |
| FR-010 | ✅ | 快照含 `task_id`、`run_id`、`status`、`step_results` | `step_results` 是 `state` 的派生只读视图 |
| FR-011 | ✅ | `apply_snapshot()` / `from_snapshot()` | 校验任务、协议、输入、路径、计数器 |
| FR-012 | ✅ | `ensure_json_safe()`；`E_STATE_SERIALIZATION` | 拒绝 tuple/set/非字符串键/自定义对象 |
| FR-013 | ✅ | `state`、`completed_steps`、`branch_routes`、`next_step_id`、计数器 | 路径一致性有专门测试 |

### 4.3 检查点管理

| 编号 | 结论 | 证据 | 说明 |
|---|---|---|---|
| FR-014 | ✅ | `CheckpointStore.save()` | 返回 `checkpoint_id` |
| FR-015 | ✅ | `load_latest(run_id, task_id)` | 按 run 分目录 |
| FR-016 | ✅🔁 | `checkpoint_version`、`task_version`、`created_at` | `task_version = spec_hash[:16]`，内容派生而非人工版本（见 F-03） |
| FR-017 | ✅ | JSON/schema/版本/plan+spec hash/`content_hash` | 能识别损坏、不完整、不兼容、被篡改 |
| FR-018 | ✅ | 临时文件 + `fsync` + `os.replace` | 未做多进程并发锁（需求未要求） |
| FR-019 | ✅ | 按 run 分目录 + `run_id` 校验 | 不同 run 不会混用 |

### 4.4 恢复执行

| 编号 | 结论 | 证据 | 说明 |
|---|---|---|---|
| FR-020 | ✅ | `TaskRuntime.resume(run_id=...)` / `checkpoint_id` | 脚本与图都暴露 `resume()` |
| FR-021 | ✅ | `load_latest` + `_resolve_resume_checkpoint` | — |
| FR-022 | ✅ | 重放遍历 + `STEP_SKIPPED`；`test_resume_skips_completed_steps`、`test_random_dag_resume_preserves_calls_and_output` | 调用计数验证，不只看输出 |
| FR-023 | ⚠️ | `task_id`、协议主版本、`plan_hash`、`spec_hash`；计划/任务变化测试 | 结构哈希不含函数体（见 F-02） |
| FR-024 | ✅ | `E_CHECKPOINT_NOT_FOUND` + `RECOVERY_FAILED` | — |
| FR-025 | ✅ | 输出 Schema、重试、步数/模型调用上限；`elapsed_seconds` 剩余预算；`test_step_timeout_parity`、`test_task_timeout_parity`、`test_model_call_limit_parity`、`test_max_steps_limit_parity` | 超时是**执行时间**预算，不含停机时间（见 F-04） |
| FR-026 | ✅⚠️ | 非幂等工具 + `UNKNOWN` → `E_RESUME_UNSUPPORTED`；`test_non_idempotent_unknown_result_blocks_resume` | **仅对声明了 `ToolSpec` 的工具生效**；`func=` 步骤默认视为可重放 |

### 4.5 双模式一致性

| 编号 | 结论 | 证据 | 说明 |
|---|---|---|---|
| FR-027 | ✅ | 两种 API 共用 `Checkpoint` 与 `TaskRuntime` | — |
| FR-028 | ✅ | 统一 `resume` 入口 | — |
| FR-029 | ✅ | `test_script_and_graph_resume_are_equivalent`、`test_branch_resume_parity`、`test_nested_branch_resume_parity` | 线性 + 单层/嵌套分支恢复等价；多 end 为图模式独有，由 `test_graph_multiple_ends_resume` 覆盖 |

---

## 2. 验收测试复核

| 用例 | 结论 | 证据/说明 |
|---|---|---|
| AT-001 正常任务 | ✅ | 原有执行测试 |
| AT-002 工具异常定位 | ✅ | 工具错误事件含 `error_code`、`step_id` |
| AT-003 快照导出/导入 | ✅ | `test_snapshot_roundtrip_and_non_json_state` |
| AT-004 检查点保存/读取 | ✅ | `test_json_checkpoint_store_roundtrip_and_latest` |
| AT-005 前两步成功、第三步失败后恢复 | ✅ | `test_at005_...`，断言 `calls == {"a":1,"b":1,"c":2}` |
| AT-006 损坏检查点 | ✅ | `test_corrupted_checkpoint_is_rejected` |
| AT-007 不兼容任务版本 | ✅ | 用 `plan_hash`/`spec_hash` 变化验证，不是 `task_version` 字段 |
| AT-008 检查点保存失败 | ✅ | `test_checkpoint_save_failure_does_not_retry_step`；`E_CHECKPOINT_IO` |
| AT-009 恢复后输出 Schema | ✅ | 恢复统一经过 `_execute_end` 校验 |
| AT-010 两种 API 等价 | ✅ | `test_script_and_graph_are_equivalent`（线性 + 单层分支） |
| AT-011 日志不含敏感凭据 | ✅ | 事件脱敏测试 + `test_checkpoint_is_encrypted_on_disk`；检查点磁盘无明文 |
| AT-012 不确定副作用不重复 | ✅ | `test_non_idempotent_unknown_result_blocks_resume`（ToolSpec 场景） |

---

## 3. 非功能需求复核

| 编号 | 结论 | 说明 |
|---|---|---|
| NFR-001 兼容性 | ✅ | 49 个 P0 用例持续通过；关闭 checkpoint 不影响原行为 |
| NFR-002 正确性 | ✅ | 快照/检查点恢复覆盖状态、步骤结果、计数器；随机 DAG 测试 |
| NFR-003 可诊断性 | ⚠️ | 恢复失败有明确错误码与 `RECOVERY_FAILED`；但预检查阶段事件 sink 失败会被吞掉（见 F-07） |
| NFR-004 安全性 | ✅ | 事件脱敏、无任意对象反序列化；checkpoint 默认 AES-GCM 加密（F-01 已修复） |
| NFR-005 可测试性 | ✅ | 存储、快照、恢复、执行器路径均有独立测试 |
| NFR-006 可维护性 | ✅ | `checkpoint_version=1`，不兼容版本显式拒绝 |
| NFR-007 性能 | ⚠️ | 已有最小基准（5/10/20 步、文件大小、恢复耗时、重复调用数）；完整对照实验留待论文实验阶段 |

---

## 4. 对照“P1 完成判定”（文档 §11）

| # | 完成判定 | 结论 | 说明 |
|---|---|---|---|
| 1 | 所有 P0 回归测试通过 | ✅ | 49 个 P0 用例 |
| 2 | 诊断事件能定位任务、步骤、尝试次数、错误类型 | ✅ | `task_id`/`step_id`/`attempt`/`error_code`/`duration_ms` |
| 3 | 快照可导出、持久化、正确恢复 | ✅ | — |
| 4 | 检查点可保存、读取，识别损坏/不兼容 | ✅ | 含 `content_hash` |
| 5 | 恢复跳过已完成步骤 | ✅ | 调用计数验证 + 随机 DAG |
| 6 | 恢复后输出通过 Schema | ✅ | — |
| 7 | 脚本/图在约定支持范围内一致 | ✅ | 线性 + 单层/嵌套分支恢复已证明；多 end 为图模式独有并已单测覆盖 |
| 8 | 副作用步骤不默认重复 | ⚠️ | ToolSpec 生效；`func=` 步骤按文档视为可重放 |
| 9 | 自动化测试、架构图、接口文档已更新 | ⚠️ | 测试 ✅、Mermaid 架构图 ✅；**没有独立接口文档**（见 F-05） |
| 10 | （隐含）失败恢复有明确错误 | ✅ | 错误码 + 事件 |
| 11 | （隐含）检查点数据可序列化、带版本 | ✅ | — |

---

## 5. 本次复核新发现的问题

### F-01 checkpoint 明文保存输入与状态（✅ 2026-11-03 已修复）

- **位置**：`executor.py:461-495` 写入 `input=snapshot["input"]`、`state=snapshot["state"]`；`checkpoint.py` 的 `Checkpoint` 模型是普通 `dict`。
- **事实（修复前）**：事件 payload 走 `redact()`；checkpoint 不走。
- **影响（修复前）**：任务输入/步骤输出中的 API Key、令牌、PII 会原样落盘。NFR-004 的原文是“不记录密钥等敏感凭据”，当时只对事件成立。
- **修复（采用加密落盘）**：
  - `JsonCheckpointStore` 默认 AES-GCM 加密，密钥优先取 `STP_CHECKPOINT_KEY`，其次 `encryption_key`，最后自动生成 `base_dir/.checkpoint.key`（0600）；
  - 检查点文件 0600、run 目录 0700；旧明文检查点仍可读取；`encrypt=False` 显式降级；
  - 不对恢复所需的 `state` 做单向脱敏，避免恢复后状态失真。
- **测试**：`test_checkpoint_is_encrypted_on_disk`、`test_checkpoint_wrong_key_is_rejected`、`test_checkpoint_encrypted_file_requires_key`、`test_checkpoint_encryption_can_be_disabled`、`test_encrypted_store_reads_legacy_plaintext_checkpoint`、`test_checkpoint_store_generates_local_key_by_default`。
- **剩余限制**：本地 key 文件不等同于 KMS；跨机器共享检查点需独立密钥与轮换策略。

### F-02 恢复不感知代码实现变化

- **位置**：`checkpoint.py:44-50`；`plan_hash`/`spec_hash` 基于 `TaskIR`/`TaskSpec` 的结构视图，`StepAction.func` 被 `exclude=True` 排除。
- **影响**：改了函数体但结构不变时，恢复会用新实现跑旧状态。README 已列为限制，但 FR-023 若按“任务定义包含实现”理解，则未完全满足。
- **建议**：增加 `TaskSpec.code_revision`（git commit 或构建号），写入并校验 checkpoint；或在论文中明确“恢复前必须固定代码版本”。

### F-03 `task_version` 的语义

- **事实**：`task_version = spec_hash[:16]`，不是人工维护的版本号。
- **评价**：作为“内容派生版本”是合理的，且比人工版本更可靠；但需求文档的示例是一个可读版本号（如 `"1.0"`）。追溯文档已说明，答辩时应主动解释，避免被认为“字段没实现”。

### F-04 超时预算口径

- **事实**：`elapsed_seconds` 累计的是执行时间，恢复后使用剩余预算；进程停机时间不计入。
- **评价**：实现比“恢复后重新计时”更严格，方向正确；但这不是墙钟时间意义上的总预算。需求 FR-025 说“继续遵守原有超时约束”，当前解释需要在论文中写清。

### F-05 缺少独立接口文档

- **事实**：README 有 API 用法，`p1_traceability.md` 有模块职责和架构图；没有单独的接口文档（类/方法签名、参数、异常、版本）。
- **评价**：验收判定写的是“架构图和接口文档已更新”。架构图满足；接口文档只能算“README 内嵌说明”，严格说未满足。
- **建议**：把 README 中的接口章节抽成 `docs/api.md`，或明确声明 README 即接口文档。

### F-06 AT-010 / FR-029 的“支持范围”需要写死（已基本闭环）

- **事实**：2026-11-04 已补执行等价测试（嵌套分支、工具失败/重试、LLM、空 case、取消、超时、限制）和**单层 + 嵌套分支恢复的脚本/图对比**（`test_branch_resume_parity`、`test_nested_branch_resume_parity`）；图模式多 end 恢复由 `test_graph_multiple_ends_resume` 覆盖。
- **评价**：AT-010 与 FR-029 在“线性 + 单层/嵌套分支”范围内已有证据；多 end 是图模式独有能力，不做脚本 parity。
- **建议**：在论文和 `dual_mode_matrix.md` 中把“支持范围”写成“线性 + 单层/嵌套分支；多 end 仅图模式”，其余（非幂等副作用、模型失败）列为后续工作。

### F-07 恢复预检查失败的事件可能被吞掉

- **位置**：`executor.py:_emit_recovery_failure()` 用 `try/except Exception: pass` 包裹事件发送。
- **评价**：这是为了不让 sink 故障掩盖原始恢复错误，方向正确；但 NFR-003 的“恢复失败应有事件记录”在 sink 故障时不成立。属于边界情况，建议在文档中说明“事件尽力而为，原始错误优先”。

### F-08 FR-008 / AT-011 的范围需要写清

- **事实**：AT-011 只检查事件日志；checkpoint 不在其范围。
- **评价**：按字面“日志”理解，AT-011 通过；按 NFR-004 “不记录密钥”理解，则不通过。两个编号的口径应统一。

---

## 6. 复核结论与建议

### 可以怎样表述

> **P1 功能需求 29 条中 26 条完全满足，2 条部分满足（实现版本、副作用范围），1 条为已说明的语义偏差；12 条验收用例 11 条满足、1 条需限定范围；112 个测试通过。**

这比“P1 全部完成”更准确，也更能经得起追问。

### 答辩前建议按顺序补

1. **F-01**：checkpoint 安全（至少加密或 SecretRef + 信任边界文档）——这是唯一可能造成真实数据泄露的项。
2. **F-06**：补“单层分支恢复的脚本/图等价”用例，并在 matrix 中固定支持范围。
3. **F-05**：补 `docs/api.md` 或明确 README 即接口文档。
4. **F-02 / F-03 / F-04**：写入论文的限制与设计取舍章节，不需要改代码也不能算“遗漏”。
5. **F-07 / F-08**：在追溯文档中把口径写清（事件尽力而为；AT-011 只覆盖日志）。

### 不需要改代码的部分

- `RecoveryManager` 命名、`step_results` 派生视图、新 `TaskRun` + lineage、`task_version` 内容派生——这些是合理设计，继续用文档解释即可。
- NFR-007 完整实验——按计划留到论文实验阶段。

---

## 7. 复核方法与可重复性

```bash
# 测试
cd stp
python -m pytest -o addopts="" -q        # 112 passed

# 需求文档转文本（本次使用 Cherry 的 docx → markdown）
# 逐条对照：
#   stp/executor.py
#   stp/checkpoint.py
#   stp/context.py
#   stp/models.py
#   stp/tests/test_checkpoint.py
#   stp/docs/p1_traceability.md
#   stp/docs/p1_acceptance_report.md
```

本次复核没有修改任何代码，只新增了本文档。
