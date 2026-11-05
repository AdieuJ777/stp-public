"""E6 T1：LangGraph 实现。"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from shared import (
    T1_INPUT,
    check_budget,
    extract_expense,
    record_expense,
    request_approval,
)


class T1State(TypedDict, total=False):
    text: str
    expense: dict
    budget: dict
    result: dict
    output: dict


def extract_node(state: T1State) -> dict:
    return {"expense": extract_expense(state["text"])}


def budget_node(state: T1State) -> dict:
    return {"budget": check_budget(state["expense"])}


def route(state: T1State) -> str:
    return (
        "approval"
        if state["expense"]["amount"] > state["budget"]["remaining"]
        else "record"
    )


def approval_node(state: T1State) -> dict:
    return {"result": request_approval(state["expense"])}


def record_node(state: T1State) -> dict:
    return {"result": record_expense(state["expense"])}


def final_node(state: T1State) -> dict:
    return {
        "output": {
            "status": state["result"]["status"],
            "expense": state["expense"],
            "result": state["result"],
        }
    }


def build_graph():
    graph = StateGraph(T1State)
    graph.add_node("extract", extract_node)
    graph.add_node("budget", budget_node)
    graph.add_node("approval", approval_node)
    graph.add_node("record", record_node)
    graph.add_node("final", final_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "budget")
    graph.add_conditional_edges(
        "budget",
        route,
        {"approval": "approval", "record": "record"},
    )
    graph.add_edge("approval", "final")
    graph.add_edge("record", "final")
    graph.add_edge("final", END)
    return graph.compile()


def run() -> dict:
    result = build_graph().invoke({"text": T1_INPUT["text"]})
    return result["output"]


if __name__ == "__main__":
    print(run())
