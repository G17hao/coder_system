"""TODO 列表工具 — 供 LLM 在工具循环中维护任务进度"""

from __future__ import annotations

from typing import Any


def todo_list_tool(
    operation: str,
    items: list[dict[str, Any]] | None = None,
    *,
    _state: list[dict[str, Any]],
) -> str:
    """操作 TODO 列表

    Args:
        operation: "write"（覆盖整个列表）或 "read"（读取当前列表）
        items: write 时提供的列表项，每项含 id/title/status
        _state: 由调用方传入的可变列表（用于跨调用保持状态）

    Returns:
        JSON 格式的当前列表字符串
    """
    import json

    if operation == "write":
        _state.clear()
        for item in (items or []):
            _state.append({
                "id": item.get("id"),
                "title": str(item.get("title", "")),
                "status": str(item.get("status", "not-started")),
            })

    return json.dumps(_state, ensure_ascii=False, indent=2)


# LLM tool_use 工具定义
TODO_LIST_TOOL_DEFINITION = {
    "name": "todo_list",
    "description": (
        "维护一个 TODO 任务列表，帮助你跟踪编码进度。\n"
        "- write：覆盖整个列表（用于初始化或更新状态）\n"
        "- read：读取当前列表\n\n"
        "建议在开始编码前 write 初始计划，每完成一项后 write 更新状态。\n"
        "状态值：not-started / in-progress / completed"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["write", "read"],
                "description": "操作类型：write 覆盖列表，read 读取列表",
            },
            "items": {
                "type": "array",
                "description": "write 操作时的列表项（read 时可省略）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "任务编号，从 1 开始",
                        },
                        "title": {
                            "type": "string",
                            "description": "任务标题（3-7 个词，简洁描述）",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["not-started", "in-progress", "completed"],
                            "description": "任务状态",
                        },
                    },
                    "required": ["id", "title", "status"],
                },
            },
        },
        "required": ["operation"],
    },
}
