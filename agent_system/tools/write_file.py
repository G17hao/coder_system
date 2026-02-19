"""文件写入工具 — LLM tool_use 可调用"""

from __future__ import annotations

from pathlib import Path


def write_file_tool(path: str | Path, content: str) -> str:
    """写入文件内容（自动创建中间目录）

    Args:
        path: 文件绝对路径
        content: 文件内容

    Returns:
        写入的文件绝对路径字符串
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p.resolve())


# LLM tool_use 工具定义
WRITE_FILE_TOOL_DEFINITION = {
    "name": "write_file",
    "description": "写入文件内容。如果文件不存在则创建，如果存在则覆盖。自动创建中间目录。",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件绝对路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的文件内容",
            },
        },
        "required": ["path", "content"],
    },
}
