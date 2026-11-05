"""E6 T1：STP 图实现。"""

from __future__ import annotations

import asyncio

from stp import GraphBuilder, GraphTask
from stp.refs import Ref

from shared import (
    T1_INPUT,
    check_budget,
    extract_expense,
    record_expense,
    request_approval,
)
from stp_t1_script import SPEC  # SPEC shared with the script implementation


def _task() -> GraphTask:
    builder = GraphBuilder(SPEC)
    extract = builder.add_step(
        "extract",
        func=lambda data: extract_expense(data["text"]),
        input={"text": Ref("input", "text")},
        output_key="expense",
    )
    budget = builder.add_step(
        "budget",
        func=lambda data: check_budget(data["expense"]),
        input={"expense": Ref("state", "expense")},
        output_key="budget",
    )
    route = builder.add_branch(
        "route",
        condition=lambda state: (
            "approval"
            if state["expense"]["amount"] > state["budget"]["remaining"]
            else "record"
        ),
        default="record",
    )
    approval = builder.add_step(
        "approval",
        func=lambda data: request_approval(data["expense"]),
        input={"expense": Ref("state", "expense")},
        output_key="result",
    )
    record = builder.add_step(
        "record",
        func=lambda data: record_expense(data["expense"]),
        input={"expense": Ref("state", "expense")},
        output_key="result",
    )
    join = builder.add_join("join")
    end = builder.add_end(
        "end",
        {
            "status": Ref("state", "result", ("status",)),
            "expense": Ref("state", "expense"),
            "result": Ref("state", "result"),
        },
    )
    builder.set_entry(extract)
    builder.add_edge(extract, budget)
    builder.add_edge(budget, route)
    builder.add_route(route, "approval", approval)
    builder.add_route(route, "record", record)
    builder.add_edge(approval, join)
    builder.add_edge(record, join)
    builder.add_edge(join, end)
    return GraphTask(SPEC, builder)


def run() -> dict:
    return asyncio.run(_task().run(T1_INPUT)).output


if __name__ == "__main__":
    print(run())
