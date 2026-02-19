"""文件搜索工具 — glob/regex 搜索，自动遵循 .gitignore 规则"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from agent_system.tools.list_directory import (
    _IGNORE_DIRS,
    _IGNORE_SUFFIXES,
    _find_gitignore,
    _is_gitignored,
)

# 默认最大返回数
_DEFAULT_MAX_RESULTS = 200


def search_file_tool(
    base_dir: str,
    pattern: str = "*",
    regex: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
    respect_gitignore: bool = True,
) -> list[str]:
    """搜索匹配模式的文件

    自动遵循 .gitignore 规则跳过被忽略的目录和文件。
    设置 respect_gitignore=False 可搜索所有文件（含被忽略的目录）。

    Args:
        base_dir: 搜索根目录
        pattern: glob 模式（如 "*.lua", "**/*.ts"）
        regex: 可选的正则表达式过滤（匹配文件路径）
        max_results: 最大返回结果数（默认 200）
        respect_gitignore: 是否遵循 .gitignore 规则（默认 True）

    Returns:
        匹配的文件路径列表（绝对路径字符串）
    """
    base = Path(base_dir)
    if not base.is_dir():
        return []

    compiled_re = re.compile(regex) if regex else None

    if respect_gitignore:
        skip_dirs = set(_IGNORE_DIRS)
        gitignore_patterns = _find_gitignore(base)
    else:
        skip_dirs = set()
        gitignore_patterns = []

    results: list[str] = []
    _search_walk(
        base, base, pattern, compiled_re,
        skip_dirs, gitignore_patterns,
        results, max_results, respect_gitignore,
    )

    return sorted(results)


def _search_walk(
    directory: Path,
    root: Path,
    pattern: str,
    compiled_re: re.Pattern[str] | None,
    skip_dirs: set[str],
    gitignore_patterns: list[re.Pattern[str]],
    results: list[str],
    max_results: int,
    respect_gitignore: bool,
) -> None:
    """递归搜索目录，跳过忽略的路径

    Args:
        directory: 当前遍历目录
        root: 搜索根目录
        pattern: glob 模式
        compiled_re: 编译后的正则（可选）
        skip_dirs: 硬编码忽略目录名集合
        gitignore_patterns: .gitignore 编译后正则列表
        results: 结果收集列表
        max_results: 最大结果数
        respect_gitignore: 是否启用忽略规则
    """
    if len(results) >= max_results:
        return

    try:
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return

    for entry in entries:
        if len(results) >= max_results:
            return

        # 跳过以 . 开头的隐藏文件/目录
        if entry.name.startswith("."):
            continue

        try:
            rel = entry.relative_to(root).as_posix()
        except ValueError:
            rel = entry.name

        if entry.is_dir():
            if entry.name in skip_dirs:
                continue
            if gitignore_patterns and _is_gitignored(entry.name, rel, gitignore_patterns):
                continue
            _search_walk(
                entry, root, pattern, compiled_re,
                skip_dirs, gitignore_patterns,
                results, max_results, respect_gitignore,
            )
        elif entry.is_file():
            if respect_gitignore and entry.suffix in _IGNORE_SUFFIXES:
                continue
            if gitignore_patterns and _is_gitignored(entry.name, rel, gitignore_patterns):
                continue
            # glob 模式匹配 (仅文件名)
            if not fnmatch.fnmatch(entry.name, pattern):
                continue
            path_str = str(entry)
            if compiled_re and not compiled_re.search(path_str):
                continue
            results.append(path_str)


# LLM tool_use 工具定义
SEARCH_FILE_TOOL_DEFINITION = {
    "name": "search_file",
    "description": (
        "搜索指定目录下匹配 glob 模式的文件。可附加正则过滤。"
        "默认遵循 .gitignore 规则，自动跳过 node_modules/build 等被忽略的目录。"
        "如需搜索被忽略目录中的文件，设置 respect_gitignore=false。"
    ),
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
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 200。防止结果过多。",
                "default": 200,
            },
            "respect_gitignore": {
                "type": "boolean",
                "description": (
                    "是否遵循 .gitignore 规则过滤，默认 true。"
                    "设为 false 可搜索所有文件（含被忽略的目录），"
                    "但可能较慢，建议同时用 pattern 缩小范围。"
                ),
                "default": True,
            },
        },
        "required": ["base_dir"],
    },
}
