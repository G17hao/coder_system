"""内容搜索工具 — 在文件内容中搜索关键字，返回匹配行+行号"""

from __future__ import annotations

import re
from pathlib import Path


def grep_content_tool(
    path: str,
    pattern: str,
    max_matches: int = 50,
) -> list[dict[str, str | int]]:
    """在单个文件中搜索匹配正则的行

    Args:
        path: 文件路径
        pattern: 正则表达式模式
        max_matches: 最大返回匹配数

    Returns:
        匹配结果列表 [{"line": 行号, "content": 行内容}]
    """
    p = Path(path)
    if not p.exists():
        return [{"line": 0, "content": f"文件不存在: {path}"}]

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [{"line": 0, "content": f"读取失败: {e}"}]

    compiled = re.compile(pattern, re.IGNORECASE)
    matches: list[dict[str, str | int]] = []

    for i, line in enumerate(content.splitlines(), start=1):
        if compiled.search(line):
            matches.append({"line": i, "content": line.rstrip()})
            if len(matches) >= max_matches:
                break

    return matches


def grep_dir_tool(
    base_dir: str,
    pattern: str,
    file_pattern: str = "*.ts",
    max_matches: int = 100,
) -> list[dict[str, str | int]]:
    """在目录下所有匹配文件中搜索内容

    Args:
        base_dir: 搜索根目录
        pattern: 正则表达式模式
        file_pattern: glob 文件名匹配模式
        max_matches: 最大返回匹配数

    Returns:
        匹配结果列表 [{"file": 文件路径, "line": 行号, "content": 行内容}]
    """
    base = Path(base_dir)
    if not base.is_dir():
        return [{"file": "", "line": 0, "content": f"目录不存在: {base_dir}"}]

    compiled = re.compile(pattern, re.IGNORECASE)
    matches: list[dict[str, str | int]] = []

    for fp in sorted(base.rglob(file_pattern)):
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for i, line in enumerate(content.splitlines(), start=1):
            if compiled.search(line):
                matches.append({
                    "file": str(fp),
                    "line": i,
                    "content": line.rstrip(),
                })
                if len(matches) >= max_matches:
                    return matches

    return matches


# LLM tool_use 工具定义
GREP_CONTENT_TOOL_DEFINITION = {
    "name": "grep_content",
    "description": (
        "在文件或目录中搜索匹配正则表达式的内容行，返回文件路径、行号和匹配行内容。"
        "比 read_file 更高效——不需要读取整个文件就能定位关键代码。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "搜索目标：单个文件路径或目录路径",
            },
            "pattern": {
                "type": "string",
                "description": "正则表达式搜索模式（大小写不敏感）",
            },
            "file_pattern": {
                "type": "string",
                "description": "当 path 为目录时，glob 过滤文件名（如 '*.ts', '*.lua'），默认 '*.ts'",
                "default": "*.ts",
            },
            "max_matches": {
                "type": "integer",
                "description": "最大返回匹配数量，默认 50",
                "default": 50,
            },
        },
        "required": ["path", "pattern"],
    },
}
