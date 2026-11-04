"""E1/E2 场景库。

每个场景同时提供脚本和图两种构建，保证：
- 同一 TaskSpec；
- 同一函数/工具语义；
- 同一 MockLLM 响应；
- 同一输入。

E1 用这些场景比较两种前端是否行为等价；E2 会复用它们做恢复实验。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from stp import (
    CalculatorTool,
    CancellationToken,
    GraphBuilder,
    GraphTask,
    MockLLM,
    TaskSpec,
    Tool,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    task,
)
from stp.refs import Ref


@dataclass
class Scenario:
    name: str
    description: str
    spec: TaskSpec
    make_script: Callable[[], tuple[Any, dict[str, Any]]]
    make_graph: Callable[[], tuple[Any, dict[str, Any]]]
    make_backend: Callable[[], Any]
    make_tools: Callable[[], ToolRegistry]
    input_data: dict[str, Any]
    strict_fields: tuple[str, ...]


def _spec(
    task_id: str,
    *,
    output_schema: dict | None = None,
    max_attempts: int = 1,
    max_steps: int = 20,
    max_model_calls: int = 20,
    timeout_seconds: float = 10.0,
) -> TaskSpec:
    if not task_id.startswith("TSK-"):
        task_id = f"TSK-{task_id}"
    return TaskSpec(
        task_id=task_id,
        name=task_id.lower(),
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        retry_policy={
            "max_attempts": max_attempts,
            "backoff_factor": 1.0,
            "initial_delay_seconds": 0.001,
        },
        max_steps=max_steps,
        max_model_calls=max_model_calls,
        timeout_seconds=timeout_seconds,
    )


def _mock_backend() -> MockLLM:
    return MockLLM(default={})


def _empty_tools() -> ToolRegistry:
    return ToolRegistry()


# ---------------------------------------------------------------------------
# 1. 线性任务
# ---------------------------------------------------------------------------
def scenario_linear() -> Scenario:
    spec = _spec("EXP-LINEAR")

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step("a", func=lambda _: {"result": "a"}, input={}, output_key="a")
            ctx.step("b", func=lambda _: {"result": "b"}, input={}, output_key="b")
            ctx.step("c", func=lambda _: {"result": "c"}, input={}, output_key="c")
            ctx.end({"result": ctx.state["c"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        a = builder.add_step("a", func=lambda _: {"result": "a"}, input={}, output_key="a")
        b = builder.add_step("b", func=lambda _: {"result": "b"}, input={}, output_key="b")
        c = builder.add_step("c", func=lambda _: {"result": "c"}, input={}, output_key="c")
        end = builder.add_end("end", {"result": Ref("state", "c", ("result",))})
        builder.set_entry(a)
        builder.add_edge(a, b)
        builder.add_edge(b, c)
        builder.add_edge(c, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="linear",
        description="三步顺序函数任务",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=("result",),
    )


# ---------------------------------------------------------------------------
# 2. 单层分支
# ---------------------------------------------------------------------------
def scenario_branch() -> Scenario:
    spec = _spec("EXP-BRANCH")

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "classify",
                func=lambda _: {"amount": 1500},
                input={},
                output_key="expense",
            )

            def high(c):
                c.step(
                    "high",
                    func=lambda _: {"result": "high"},
                    input={},
                    output_key="result",
                )

            def low(c):
                c.step(
                    "low",
                    func=lambda _: {"result": "low"},
                    input={},
                    output_key="result",
                )

            ctx.branch(
                "route",
                condition=lambda state: (
                    "high" if state["expense"]["amount"] > 1000 else "low"
                ),
                cases={"high": high, "low": low},
                default="low",
            )
            ctx.end({"result": ctx.state["result"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        classify = builder.add_step(
            "classify",
            func=lambda _: {"amount": 1500},
            input={},
            output_key="expense",
        )
        route = builder.add_branch(
            "route",
            condition=lambda state: (
                "high" if state["expense"]["amount"] > 1000 else "low"
            ),
            default="low",
        )
        high = builder.add_step(
            "high", func=lambda _: {"result": "high"}, input={}, output_key="result"
        )
        low = builder.add_step(
            "low", func=lambda _: {"result": "low"}, input={}, output_key="result"
        )
        join = builder.add_join("join")
        end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})
        builder.set_entry(classify)
        builder.add_edge(classify, route)
        builder.add_route(route, "high", high)
        builder.add_route(route, "low", low)
        builder.add_edge(high, join)
        builder.add_edge(low, join)
        builder.add_edge(join, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="branch",
        description="单层条件分支 + 汇合",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=("result",),
    )


# ---------------------------------------------------------------------------
# 3. 嵌套分支
# ---------------------------------------------------------------------------
def scenario_nested_branch() -> Scenario:
    spec = _spec("EXP-NESTED")

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "classify",
                func=lambda _: {"amount": 1500, "region": "EU"},
                input={},
                output_key="expense",
            )

            def high(c):
                def eu(c2):
                    c2.step(
                        "high_eu",
                        func=lambda _: {"result": "high-eu"},
                        input={},
                        output_key="result",
                    )

                def other(c2):
                    c2.step(
                        "high_other",
                        func=lambda _: {"result": "high-other"},
                        input={},
                        output_key="result",
                    )

                c.branch(
                    "inner",
                    condition=lambda state: (
                        "eu" if state["expense"]["region"] == "EU" else "other"
                    ),
                    cases={"eu": eu, "other": other},
                    default="other",
                )

            def low(c):
                c.step(
                    "low",
                    func=lambda _: {"result": "low"},
                    input={},
                    output_key="result",
                )

            ctx.branch(
                "outer",
                condition=lambda state: (
                    "high" if state["expense"]["amount"] > 1000 else "low"
                ),
                cases={"high": high, "low": low},
                default="low",
            )
            ctx.end({"result": ctx.state["result"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        classify = builder.add_step(
            "classify",
            func=lambda _: {"amount": 1500, "region": "EU"},
            input={},
            output_key="expense",
        )
        outer = builder.add_branch(
            "outer",
            condition=lambda state: (
                "high" if state["expense"]["amount"] > 1000 else "low"
            ),
            default="low",
        )
        inner = builder.add_branch(
            "inner",
            condition=lambda state: (
                "eu" if state["expense"]["region"] == "EU" else "other"
            ),
            default="other",
        )
        high_eu = builder.add_step(
            "high_eu", func=lambda _: {"result": "high-eu"}, input={}, output_key="result"
        )
        high_other = builder.add_step(
            "high_other",
            func=lambda _: {"result": "high-other"},
            input={},
            output_key="result",
        )
        low = builder.add_step(
            "low", func=lambda _: {"result": "low"}, input={}, output_key="result"
        )
        inner_join = builder.add_join("inner_join")
        outer_join = builder.add_join("outer_join")
        end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})
        builder.set_entry(classify)
        builder.add_edge(classify, outer)
        builder.add_route(outer, "high", inner)
        builder.add_route(outer, "low", low)
        builder.add_route(inner, "eu", high_eu)
        builder.add_route(inner, "other", high_other)
        builder.add_edge(high_eu, inner_join)
        builder.add_edge(high_other, inner_join)
        builder.add_edge(inner_join, outer_join)
        builder.add_edge(low, outer_join)
        builder.add_edge(outer_join, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="nested_branch",
        description="两层嵌套分支 + 双汇合",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=("result",),
    )


# ---------------------------------------------------------------------------
# 4. 空分支 case
# ---------------------------------------------------------------------------
def scenario_empty_case() -> Scenario:
    spec = _spec("EXP-EMPTY")

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "base",
                func=lambda _: {"result": "base"},
                input={},
                output_key="base",
            )

            def empty(c):
                pass

            def work(c):
                c.step(
                    "work",
                    func=lambda _: {"result": "work"},
                    input={},
                    output_key="unused",
                )

            ctx.branch(
                "route",
                condition=lambda state: "empty",
                cases={"empty": empty, "work": work},
                default="work",
            )
            ctx.end({"result": ctx.state["base"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        base = builder.add_step(
            "base", func=lambda _: {"result": "base"}, input={}, output_key="base"
        )
        route = builder.add_branch(
            "route", condition=lambda state: "empty", default="work"
        )
        work = builder.add_step(
            "work", func=lambda _: {"result": "work"}, input={}, output_key="unused"
        )
        join = builder.add_join("route_join")
        end = builder.add_end("end", {"result": Ref("state", "base", ("result",))})
        builder.set_entry(base)
        builder.add_edge(base, route)
        builder.add_route(route, "empty", join)
        builder.add_route(route, "work", work)
        builder.add_edge(work, join)
        builder.add_edge(join, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="empty_case",
        description="空分支 case 直接走汇合",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=("result",),
    )


# ---------------------------------------------------------------------------
# 5. 工具成功
# ---------------------------------------------------------------------------
def scenario_tool_success() -> Scenario:
    spec = _spec(
        "EXP-TOOL-OK",
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "calc",
                tool="calculator",
                input={"expression": "sqrt(16) + 2 * 3"},
                output_key="calc",
            )
            ctx.end({"value": ctx.state["calc"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        calc = builder.add_step(
            "calc",
            tool="calculator",
            input={"expression": "sqrt(16) + 2 * 3"},
            output_key="calc",
        )
        end = builder.add_end("end", {"value": Ref("state", "calc", ("result",))})
        builder.set_entry(calc)
        builder.add_edge(calc, end)
        return GraphTask(spec, builder), {}

    def make_tools() -> ToolRegistry:
        return ToolRegistry([CalculatorTool()])

    return Scenario(
        name="tool_success",
        description="计算器工具成功调用",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=make_tools,
        input_data={"x": 1},
        strict_fields=("value",),
    )


# ---------------------------------------------------------------------------
# 6. 工具失败 + 重试
# ---------------------------------------------------------------------------
class _AlwaysFailTool(Tool):
    spec = ToolSpec(
        tool_name="always_fail",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        side_effect=False,
        idempotent=True,
    )

    async def execute(self, input_data, context):
        raise ToolExecutionError("boom")


def scenario_tool_failure_retry() -> Scenario:
    spec = _spec("EXP-TOOL-FAIL", max_attempts=2)

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step("call", tool="always_fail", input={}, output_key="v")
            ctx.end({"result": ctx.state["v"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        call = builder.add_step("call", tool="always_fail", input={}, output_key="v")
        end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
        builder.set_entry(call)
        builder.add_edge(call, end)
        return GraphTask(spec, builder), {}

    def make_tools() -> ToolRegistry:
        return ToolRegistry([_AlwaysFailTool()])

    return Scenario(
        name="tool_failure_retry",
        description="工具始终失败，重试一次后任务失败",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=make_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


# ---------------------------------------------------------------------------
# 7. LLM 结构化输出
# ---------------------------------------------------------------------------
def _llm_schema() -> dict:
    return {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def scenario_llm_structured() -> Scenario:
    spec = _spec("EXP-LLM", output_schema=_llm_schema())

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "extract",
                llm="return a value",
                output_schema=_llm_schema(),
                output_key="data",
            )
            ctx.end({"value": ctx.state["data"]["value"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        extract = builder.add_step(
            "extract",
            llm="return a value",
            output_schema=_llm_schema(),
            output_key="data",
        )
        end = builder.add_end("end", {"value": Ref("state", "data", ("value",))})
        builder.set_entry(extract)
        builder.add_edge(extract, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="llm_structured",
        description="MockLLM 结构化输出",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=lambda: MockLLM([{"value": 7}]),
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=("value",),
    )


# ---------------------------------------------------------------------------
# 8. 步骤超时
# ---------------------------------------------------------------------------
async def _slow_async(_data):
    await asyncio.sleep(0.2)
    return {"result": "late"}


def scenario_step_timeout() -> Scenario:
    spec = _spec("EXP-STEP-TIMEOUT")

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "slow",
                func=_slow_async,
                input={},
                output_key="v",
                timeout_seconds=0.05,
            )
            ctx.end({"result": ctx.state["v"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        slow = builder.add_step(
            "slow",
            func=_slow_async,
            input={},
            output_key="v",
            timeout_seconds=0.05,
        )
        end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
        builder.set_entry(slow)
        builder.add_edge(slow, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="step_timeout",
        description="步骤级超时",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


# ---------------------------------------------------------------------------
# 9. 任务超时
# ---------------------------------------------------------------------------
def scenario_task_timeout() -> Scenario:
    spec = _spec("EXP-TASK-TIMEOUT", timeout_seconds=0.05)

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step("slow", func=_slow_async, input={}, output_key="v")
            ctx.end({"result": ctx.state["v"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        slow = builder.add_step("slow", func=_slow_async, input={}, output_key="v")
        end = builder.add_end("end", {"result": Ref("state", "v", ("result",))})
        builder.set_entry(slow)
        builder.add_edge(slow, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="task_timeout",
        description="任务级超时",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


# ---------------------------------------------------------------------------
# 10. 模型调用上限
# ---------------------------------------------------------------------------
def scenario_model_limit() -> Scenario:
    spec = _spec(
        "EXP-MODEL-LIMIT",
        output_schema=_llm_schema(),
        max_model_calls=1,
    )

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step("llm1", llm="first", output_schema=_llm_schema(), output_key="a")
            ctx.step("llm2", llm="second", output_schema=_llm_schema(), output_key="b")
            ctx.end({"value": ctx.state["b"]["value"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        first = builder.add_step(
            "llm1", llm="first", output_schema=_llm_schema(), output_key="a"
        )
        second = builder.add_step(
            "llm2", llm="second", output_schema=_llm_schema(), output_key="b"
        )
        end = builder.add_end("end", {"value": Ref("state", "b", ("value",))})
        builder.set_entry(first)
        builder.add_edge(first, second)
        builder.add_edge(second, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="model_limit",
        description="模型调用次数上限",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=lambda: MockLLM([{"value": 1}, {"value": 2}]),
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


# ---------------------------------------------------------------------------
# 11. 步数上限
# ---------------------------------------------------------------------------
def scenario_step_limit() -> Scenario:
    spec = _spec("EXP-STEP-LIMIT", max_steps=2)

    def make_script():
        @task(spec)
        def build(ctx):
            ctx.step(
                "base",
                func=lambda _: {"result": "base"},
                input={},
                output_key="base",
            )

            def high(c):
                c.step(
                    "high",
                    func=lambda _: {"result": "high"},
                    input={},
                    output_key="result",
                )

            def low(c):
                c.step(
                    "low",
                    func=lambda _: {"result": "low"},
                    input={},
                    output_key="result",
                )

            ctx.branch(
                "route",
                condition=lambda state: "high",
                cases={"high": high, "low": low},
                default="low",
            )
            ctx.end({"result": ctx.state["result"]["result"]})

        return build, {}

    def make_graph():
        builder = GraphBuilder(spec)
        base = builder.add_step(
            "base", func=lambda _: {"result": "base"}, input={}, output_key="base"
        )
        route = builder.add_branch(
            "route", condition=lambda state: "high", default="low"
        )
        high = builder.add_step(
            "high", func=lambda _: {"result": "high"}, input={}, output_key="result"
        )
        low = builder.add_step(
            "low", func=lambda _: {"result": "low"}, input={}, output_key="result"
        )
        join = builder.add_join("join")
        end = builder.add_end("end", {"result": Ref("state", "result", ("result",))})
        builder.set_entry(base)
        builder.add_edge(base, route)
        builder.add_route(route, "high", high)
        builder.add_route(route, "low", low)
        builder.add_edge(high, join)
        builder.add_edge(low, join)
        builder.add_edge(join, end)
        return GraphTask(spec, builder), {}

    return Scenario(
        name="step_limit",
        description="步数上限（含 branch 计数口径）",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


# ---------------------------------------------------------------------------
# 12. 取消
# ---------------------------------------------------------------------------
def scenario_cancel() -> Scenario:
    spec = _spec("EXP-CANCEL")

    def make_script():
        token = CancellationToken()

        @task(spec)
        def build(ctx):
            def first(_data):
                token.cancel()
                return {"result": "first"}

            def second(_data):
                return {"result": "second"}

            ctx.step("first", func=first, input={}, output_key="first")
            ctx.step("second", func=second, input={}, output_key="second")
            ctx.end({"result": ctx.state["second"]["result"]})

        return build, {"cancellation": token}

    def make_graph():
        token = CancellationToken()
        builder = GraphBuilder(spec)

        def first(_data):
            token.cancel()
            return {"result": "first"}

        def second(_data):
            return {"result": "second"}

        first_node = builder.add_step("first", func=first, input={}, output_key="first")
        second_node = builder.add_step("second", func=second, input={}, output_key="second")
        end = builder.add_end("end", {"result": Ref("state", "second", ("result",))})
        builder.set_entry(first_node)
        builder.add_edge(first_node, second_node)
        builder.add_edge(second_node, end)
        return GraphTask(spec, builder), {"cancellation": token}

    return Scenario(
        name="cancel",
        description="步骤边界取消",
        spec=spec,
        make_script=make_script,
        make_graph=make_graph,
        make_backend=_mock_backend,
        make_tools=_empty_tools,
        input_data={"x": 1},
        strict_fields=(),
    )


SCENARIOS = [
    scenario_linear(),
    scenario_branch(),
    scenario_nested_branch(),
    scenario_empty_case(),
    scenario_tool_success(),
    scenario_tool_failure_retry(),
    scenario_llm_structured(),
    scenario_step_timeout(),
    scenario_task_timeout(),
    scenario_model_limit(),
    scenario_step_limit(),
    scenario_cancel(),
]
