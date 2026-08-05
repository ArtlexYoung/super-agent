"""Optional bounded standard-library tools for general Agent tasks."""

from __future__ import annotations

import math
from dataclasses import replace

from core.checks import ActionEffect
from super_agent import Agent


MAX_NUMBER_COUNT = 1_000
MAX_TEXT_CHARS = 100_000
MAX_TEXT_MATCHES = 100


class GeneralToolServer:
    """Expose small pure operations through the existing MCP Skill mechanism."""

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": "calculate_numbers",
                "description": "Calculate sum, mean, minimum, maximum, or product.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["sum", "mean", "minimum", "maximum", "product"],
                        },
                        "values": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["operation", "values"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "find_text",
                "description": "Find bounded literal text positions without regular expressions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["text", "query"],
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        if name == "calculate_numbers":
            return _calculate_numbers(arguments)
        if name == "find_text":
            return _find_text(arguments)
        raise KeyError(f"general tool not found: {name}")


def attach_general_tools_to_agent(agent: Agent) -> None:
    """Explicitly register and enable the optional general tool Skill."""
    agent.add_tool(
        "general",
        GeneralToolServer(),
        effects=(ActionEffect.EXECUTE,),
    )
    if "mcp:general" not in agent.config.agent.skills:
        agent._replace_configuration(
            replace(
                agent.config,
                agent=replace(
                    agent.config.agent,
                    skills=[*agent.config.agent.skills, "mcp:general"],
                ),
            )
        )


def _calculate_numbers(arguments: dict[str, object]) -> dict[str, object]:
    operation = arguments.get("operation")
    values = arguments.get("values")
    if operation not in {"sum", "mean", "minimum", "maximum", "product"}:
        raise ValueError("calculate_numbers operation is invalid")
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_NUMBER_COUNT:
        raise ValueError(f"calculate_numbers requires 1 to {MAX_NUMBER_COUNT} values")
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in values):
        raise TypeError("calculate_numbers values must be numbers")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError("calculate_numbers values must be finite")
    functions = {
        "sum": math.fsum,
        "mean": lambda items: math.fsum(items) / len(items),
        "minimum": min,
        "maximum": max,
        "product": math.prod,
    }
    result = functions[operation](numbers)
    if not math.isfinite(result):
        raise OverflowError("calculate_numbers result is not finite")
    return {"operation": operation, "count": len(numbers), "result": result}


def _find_text(arguments: dict[str, object]) -> dict[str, object]:
    text = arguments.get("text")
    query = arguments.get("query")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"find_text text must be at most {MAX_TEXT_CHARS} characters")
    if not isinstance(query, str) or not query:
        raise ValueError("find_text query cannot be empty")
    positions: list[int] = []
    offset = 0
    while len(positions) < MAX_TEXT_MATCHES:
        position = text.find(query, offset)
        if position < 0:
            break
        positions.append(position)
        offset = position + len(query)
    return {
        "query": query,
        "positions": positions,
        "truncated": len(positions) == MAX_TEXT_MATCHES and text.find(query, offset) >= 0,
    }
