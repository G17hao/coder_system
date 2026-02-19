"""目录列表工具 — 返回目录树结构"""

from __future__ import annotations

from pathlib import Path

# 默认忽略的目录名
_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", "library", "temp", ".vscode", ".idea",
    "profiles", "remote",
}

# 默认忽略的文件后缀
_IGNORE_SUFFIXES = {".meta", ".pyc", ".pyo"}


def list_directory_tool(
    path: str,
    max_depth: int = 3,
    include_files: bool = True,
    ignore_dirs: list[str] | None = None,
) -> str:
    """列出目录树结构

    Args:
        path: 目录绝对路径
        max_depth: 最大递归深度（默认 3）
        include_files: 是否包含文件（False 则只显示目录）
        ignore_dirs: 额外忽略的目录名列表

    Returns:
        缩进格式的目录树字符串
    """
    root = Path(path)
    if not root.is_dir():
        return f"目录不存在: {path}"

    skip_dirs = set(_IGNORE_DIRS)
    if ignore_dirs:
        skip_dirs.update(ignore_dirs)

    lines: list[str] = [f"{root.name}/"]
    _walk(root, lines, depth=1, max_depth=max_depth,
          include_files=include_files, skip_dirs=skip_dirs)

    return "\n".join(lines)


def _walk(
    directory: Path,
    lines: list[str],
    depth: int,
    max_depth: int,
    include_files: bool,
    skip_dirs: set[str],
) -> None:
    """递归构建目录树"""
    if depth > max_depth:
        return

    indent = "  " * depth
    entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))

    for entry in entries:
        if entry.name.startswith(".") and entry.name not in (".gitkeep",):
            continue

        if entry.is_dir():
            if entry.name in skip_dirs:
                continue
            lines.append(f"{indent}{entry.name}/")
            _walk(entry, lines, depth + 1, max_depth, include_files, skip_dirs)
        elif include_files:
            if entry.suffix in _IGNORE_SUFFIXES:
                continue
            lines.append(f"{indent}{entry.name}")


# LLM tool_use 工具定义
LIST_DIRECTORY_TOOL_DEFINITION = {
    "name": "list_directory",
    "description": (
        "列出目录的树形结构，包含子目录和文件。"
        "自动忽略 node_modules/.git 等无关目录。"
        "用于快速了解项目结构，比反复调用 search_file 更高效。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录的绝对路径",
            },
            "max_depth": {
                "type": "integer",
                "description": "最大递归深度，默认 3",
                "default": 3,
            },
            "include_files": {
                "type": "boolean",
                "description": "是否包含文件（false 则只显示目录结构），默认 true",
                "default": True,
            },
        },
        "required": ["path"],
    },
}
