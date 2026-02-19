"""文件读取工具 — LLM tool_use 可调用"""

from __future__ import annotations

from pathlib import Path


def read_file_tool(path: str, start: int = 1, end: int | None = None) -> str:
    """读取指定文件的内容（或指定行范围）

    Args:
        path: 文件绝对路径或相对路径
        start: 起始行号（1-based，含）
        end: 结束行号（1-based，含）；None 表示到文件末尾

    Returns:
        文件内容字符串

    Raises:
        FileNotFoundError: 文件不存在
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")

    content = p.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)

    start_idx = max(0, start - 1)
    end_idx = end if end is not None else len(lines)
    selected = lines[start_idx:end_idx]

    return "".join(selected)


# LLM tool_use 工具定义
READ_FILE_TOOL_DEFINITION = {
    "name": "read_file",
    "description": "读取指定文件的内容。可以指定行范围（start/end 均为 1-based，含）。",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（绝对路径或相对于项目根目录）",
            },
            "start": {
                "type": "integer",
                "description": "起始行号（1-based，含），默认 1",
                "default": 1,
            },
            "end": {
                "type": "integer",
                "description": "结束行号（1-based，含），不指定则读取到文件末尾",
            },
        },
        "required": ["path"],
    },
}
