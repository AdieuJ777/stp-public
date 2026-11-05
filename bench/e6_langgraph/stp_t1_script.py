"""E6 T1：STP 脚本实现。"""

from __future__ import annotations

import asyncio

from stp import TaskSpec, task

from shared import (
    T1_INPUT,
    check_budget,
    extract_expense,
    record_expense,
    request_approval,
)

SPEC = TaskSpec(
    task_id="TSK-E6-T1",
    name="e6_t1",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "expense": {"type": "object"},
            "result": {"type": "object"},
        },
        "required": ["status", "expense", "result"],
        "additionalProperties": False,
    },
    max_steps=10,
    timeout_seconds=10.0,
)


@task(SPEC)
def build(ctx):
    ctx.step(
        "extract",
        func=lambda data: extract_expense(data["text"]),
        input={"text": ctx.input["text"]},
        output_key="expense",
    )
    ctx.step(
        "budget",
        func=lambda data: check_budget(data["expense"]),
        input={"expense": ctx.state["expense"]},
        output_key="budget",
    )

    def approval(c):
        c.step(
            "approval",
            func=lambda data: request_approval(data["expense"]),
            input={"expense": c.state["expense"]},
            output_key="result",
        )

    def record(c):
        c.step(
            "record",
            func=lambda data: record_expense(data["expense"]),
            input={"expense": c.state["expense"]},
            output_key="result",
        )

    ctx.branch(
        "route",
        condition=lambda state: (
            "approval"
            if state["expense"]["amount"] > state["budget"]["remaining"]
            else "record"
        ),
        cases={"approval": approval, "record": record},
        default="record",
    )
    ctx.end(
        {
            "status": ctx.state["result"]["status"],
            "expense": ctx.state["expense"],
            "result": ctx.state["result"],
        }
    )


def run() -> dict:
    return asyncio.run(build.run(T1_INPUT)).output


if __name__ == "__main__":
    print(run())
