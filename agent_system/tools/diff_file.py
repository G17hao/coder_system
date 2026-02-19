"""文件差异对比工具 — 对比两个文件或同文件修改前后"""

from __future__ import annotations

import difflib
from pathlib import Path


def diff_file_tool(
    file_a: str,
    file_b: str,
    context_lines: int = 3,
) -> str:
    """对比两个文件的差异

    Args:
        file_a: 文件 A 路径（通常是原始文件）
        file_b: 文件 B 路径（通常是修改后文件）
        context_lines: 上下文行数

    Returns:
        unified diff 格式字符串，无差异时返回空字符串
    """
    path_a = Path(file_a)
    path_b = Path(file_b)

    if not path_a.exists():
        return f"文件不存在: {file_a}"
    if not path_b.exists():
        return f"文件不存在: {file_b}"

    try:
        lines_a = path_a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = path_b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        return f"读取失败: {e}"

    diff = difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=file_a,
        tofile=file_b,
        n=context_lines,
    )

    result = "".join(diff)
    return result if result else "无差异"


def diff_content_tool(
    original: str,
    modified: str,
    label: str = "file",
    context_lines: int = 3,
) -> str:
    """对比两段文本内容的差异

    Args:
        original: 原始内容
        modified: 修改后内容
        label: 文件标签名
        context_lines: 上下文行数

    Returns:
        unified diff 格式字符串
    """
    lines_a = original.splitlines(keepends=True)
    lines_b = modified.splitlines(keepends=True)

    diff = difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"{label} (original)",
        tofile=f"{label} (modified)",
        n=context_lines,
    )

    result = "".join(diff)
    return result if result else "无差异"


# LLM tool_use 工具定义
DIFF_FILE_TOOL_DEFINITION = {
    "name": "diff_file",
    "description": (
        "对比两个文件的差异，输出 unified diff 格式。"
        "适合 Reviewer 审查代码变更，或 Coder 验证修改是否正确。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_a": {
                "type": "string",
                "description": "原始文件路径",
            },
            "file_b": {
                "type": "string",
                "description": "修改后文件路径",
            },
            "context_lines": {
                "type": "integer",
                "description": "差异上下文行数，默认 3",
                "default": 3,
            },
        },
        "required": ["file_a", "file_b"],
    },
}
