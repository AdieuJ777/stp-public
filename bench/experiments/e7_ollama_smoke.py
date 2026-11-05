"""E7 真实模型冒烟验证（Ollama + OpenAI 兼容接口）。

这不是主实验：只验证“真实模型的结构化输出能够进入统一运行时并完成任务”。
结果单独保存在 results/e7_ollama_smoke.*，不并入 E1–E6 的冻结数据。
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stp import (
    OpenAICompatBackend,
    TaskRuntime,
    TaskSpec,
    Tool,
    ToolRegistry,
    ToolSpec,
    task,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"
MODEL = "qwen2.5:1.5b"
BASE_URL = "http://localhost:11434/v1"

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["travel", "meal", "other"]},
        "amount": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["category", "amount", "note"],
    "additionalProperties": False,
}

TASK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "category": {"type": "string"},
        "amount": {"type": "number"},
    },
    "required": ["status", "category", "amount"],
    "additionalProperties": False,
}

SPEC = TaskSpec(
    task_id="TSK-OLLAMA-SMOKE",
    name="ollama-smoke",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    output_schema=TASK_OUTPUT_SCHEMA,
    retry_policy={"max_attempts": 1, "backoff_factor": 1.0},
    max_steps=10,
    max_model_calls=3,
    timeout_seconds=120.0,
)


class RecordTool(Tool):
    spec = ToolSpec(
        tool_name="record_expense",
        input_schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}, "note": {"type": "string"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
    )

    async def execute(self, input_data, context):
        return {"status": "recorded"}


class ApprovalTool(Tool):
    spec = ToolSpec(
        tool_name="request_approval",
        input_schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}, "note": {"type": "string"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        },
    )

    async def execute(self, input_data, context):
        return {"status": "pending_approval"}


@task(SPEC)
def build(ctx):
    ctx.step(
        "extract",
        llm=[
            {
                "role": "system",
                "content": (
                    "Extract expense information from the user text. "
                    "Return JSON with category (travel for transportation/lodging, "
                    "meal for food, other otherwise), amount (JSON number, not a string), "
                    "and note (string). Output JSON only."
                ),
            },
            {"role": "user", "content": ctx.input["text"]},
        ],
        output_schema=EXTRACT_SCHEMA,
        output_key="expense",
        timeout_seconds=120.0,
    )

    def case(input_builder):
        amount = input_builder.state["expense"]["amount"]
        note = input_builder.state["expense"]["note"]
        return {"amount": amount, "note": note}

    ctx.branch(
        "route",
        condition=lambda state: "high" if state["expense"]["amount"] > 500 else "low",
        cases={
            "high": lambda c: c.step(
                "approve",
                tool="request_approval",
                input=case(c),
                output_key="decision",
            ),
            "low": lambda c: c.step(
                "record",
                tool="record_expense",
                input=case(c),
                output_key="decision",
            ),
        },
        default="low",
    )

    ctx.end(
        {
            "status": ctx.state["decision"]["status"],
            "category": ctx.state["expense"]["category"],
            "amount": ctx.state["expense"]["amount"],
        }
    )


def _count_retries(events) -> int:
    return sum(1 for event in events if event.event_type.value == "RETRY_SCHEDULED")


async def main() -> int:
    results: dict[str, Any] = {
        "model": MODEL,
        "base_url": BASE_URL,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "stp_version": importlib.metadata.version("stp"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    backend = OpenAICompatBackend(
        model=MODEL,
        api_key="ollama",
        base_url=BASE_URL,
        timeout=120.0,
        supports_json_schema=True,
    )
    runs: list[dict[str, Any]] = []
    for index in range(3):
        runtime = TaskRuntime(
            backend=backend,
            tools=ToolRegistry([RecordTool(), ApprovalTool()]),
        )
        started = time.perf_counter()
        try:
            run = await build.run(
                {"text": "Taxi from airport to hotel, 128 CNY"}, runtime=runtime
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            events = runtime.events_for(run.run_id)
            runs.append(
                {
                    "run": index + 1,
                    "status": run.status.value,
                    "output": run.output,
                    "error": run.error.code if run.error else None,
                    "model_call_count": run.model_call_count,
                    "retries": _count_retries(events),
                    "event_count": len(events),
                    "elapsed_ms": round(elapsed_ms, 2),
                }
            )
        except Exception as exc:  # 冒烟脚本保留现场，便于判断环境问题
            runs.append(
                {
                    "run": index + 1,
                    "status": "CRASH",
                    "output": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "model_call_count": 0,
                    "retries": 0,
                    "event_count": 0,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )

    successes = [item for item in runs if item["status"] == "SUCCEEDED"]
    elapsed_values = sorted(item["elapsed_ms"] for item in runs)
    median_ms = elapsed_values[len(elapsed_values) // 2]
    results.update(
        {
            "status": "SUCCEEDED" if len(successes) == len(runs) else "FAILED",
            "success_rate": f"{len(successes)}/{len(runs)}",
            "median_elapsed_ms": median_ms,
            "runs": runs,
        }
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "e7_ollama_smoke.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# E7 真实模型冒烟验证（Ollama）",
        "",
        f"- 模型：`{MODEL}`",
        f"- 接口：`{BASE_URL}`",
        f"- Python：{results['python']}",
        f"- 平台：{results['platform']}",
        f"- stp：{results['stp_version']}",
        "",
        "| 运行 | 状态 | 输出 | 模型调用 | 重试 | 事件数 | 耗时 ms |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for item in runs:
        output = json.dumps(item["output"], ensure_ascii=False)
        lines.append(
            f"| {item['run']} | {item['status']} | `{output}` | "
            f"{item['model_call_count']} | {item['retries']} | "
            f"{item['event_count']} | {item['elapsed_ms']} |"
        )
    lines += [
        "",
        f"- 成功率：{results['success_rate']}",
        f"- 耗时中位数：{results['median_elapsed_ms']} ms",
        "",
        "> 这是可用性冒烟验证，不是系统性评估；主实验 E1–E6 仍使用 MockLLM。",
        "",
    ]
    (RESULTS_DIR / "e7_ollama_smoke.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
