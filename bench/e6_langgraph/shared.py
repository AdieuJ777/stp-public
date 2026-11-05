"""E6 共享业务逻辑。

STP 和 LangGraph 两套实现都调用这些函数，确保：
- 抽取/预算/审批/记账逻辑完全相同；
- LOC 比较的是“编排 + 契约 + 恢复”，不是业务函数本身。
"""

from __future__ import annotations

from typing import Any

T1_INPUT = {"text": "机票 1200 元（差旅）"}
T1_EXPECTED = {
    "status": "pending_approval",
    "expense": {
        "amount": 1200.0,
        "category": "travel",
        "vendor": "Airline",
        "description": "机票 1200 元（差旅）",
    },
    "result": {"status": "pending_approval", "request_id": "APR-1"},
}

T2_EXPECTED = {"value": 5}


def extract_expense(text: str) -> dict[str, Any]:
    """确定性“LLM 抽取”替身。"""
    return {
        "amount": 1200.0,
        "category": "travel",
        "vendor": "Airline",
        "description": text,
    }


def check_budget(expense: dict[str, Any]) -> dict[str, Any]:
    return {"remaining": 500.0}


def request_approval(expense: dict[str, Any]) -> dict[str, Any]:
    return {"status": "pending_approval", "request_id": "APR-1"}


def record_expense(expense: dict[str, Any]) -> dict[str, Any]:
    return {"status": "recorded", "entry_id": "EXP-1"}


def step_fn(index: int, calls: dict[str, int], *, fail_at: int = 3):
    """T2 的确定性步骤；第 fail_at 步第一次调用抛错。"""

    def fn(_data):
        name = f"s{index}"
        calls[name] = calls.get(name, 0) + 1
        if index == fail_at and calls[name] == 1:
            raise RuntimeError(f"injected failure at {name}")
        return {"value": index}

    return fn
