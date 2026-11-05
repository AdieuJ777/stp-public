"""E6 T2：LangGraph 失败 + 恢复实现（SQLite checkpointer）。"""

from __future__ import annotations

import os
import tempfile
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from shared import step_fn


class T2State(TypedDict):
    value: int


def build_graph(calls: dict[str, int], checkpointer):
    graph = StateGraph(T2State)
    for index in range(1, 6):
        graph.add_node(f"s{index}", step_fn(index, calls))
    graph.add_edge(START, "s1")
    for index in range(1, 5):
        graph.add_edge(f"s{index}", f"s{index + 1}")
    graph.add_edge("s5", END)
    return graph.compile(checkpointer=checkpointer)


def run() -> dict:
    calls: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="e6-lg-t2-") as temp:
        path = os.path.join(temp, "checkpoints.sqlite")
        with SqliteSaver.from_conn_string(path) as checkpointer:
            app = build_graph(calls, checkpointer)
            config = {"configurable": {"thread_id": "e6-t2"}}
            first_status = "SUCCEEDED"
            try:
                app.invoke({"value": 0}, config)
            except RuntimeError:
                first_status = "FAILED"
            resumed = app.invoke(None, config)
    return {
        "first_status": first_status,
        "resume_status": "SUCCEEDED",
        "output": {"value": resumed["value"]},
        "calls": calls,
    }


if __name__ == "__main__":
    print(run())
