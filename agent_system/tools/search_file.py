"""文件搜索工具 — glob/regex 搜索"""

from __future__ import annotations

import re
from pathlib import Path


def search_file_tool(
    base_dir: str,
    pattern: str = "*",
    regex: str | None = None,
) -> list[str]:
    """搜索匹配模式的文件

    Args:
        base_dir: 搜索根目录
        pattern: glob 模式（如 "*.lua", "**/*.ts"）
        regex: 可选的正则表达式过滤（匹配文件路径）

    Returns:
        匹配的文件路径列表（绝对路径字符串）
    """
    base = Path(base_dir)
    if not base.is_dir():
        return []

    results: list[str] = []
    compiled_re = re.compile(regex) if regex else None

    for p in base.rglob(pattern):
        if not p.is_file():
            continue
        path_str = str(p)
        if compiled_re and not compiled_re.search(path_str):
            continue
        results.append(path_str)

    return sorted(results)


# LLM tool_use 工具定义
SEARCH_FILE_TOOL_DEFINITION = {
    "name": "search_file",
    "description": "搜索指定目录下匹配 glob 模式的文件。可附加正则过滤。",
    "input_schema": {
        "type": "object",
        "properties": {
            "base_dir": {
                "type": "string",
                "description": "搜索根目录的绝对路径",
            },
            "pattern": {
                "type": "string",
                "description": "glob 模式，如 '*.lua', '*.ts'",
                "default": "*",
            },
            "regex": {
                "type": "string",
                "description": "可选的正则表达式，用于进一步过滤路径",
            },
        },
        "required": ["base_dir"],
    },
}
