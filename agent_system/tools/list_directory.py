"""目录列表工具 — 返回目录树结构，支持 .gitignore"""

from __future__ import annotations

import re
from pathlib import Path

# 默认忽略的目录名
_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", "library", "temp", ".vscode", ".idea",
    "profiles", "remote",
}

# 默认忽略的文件后缀
_IGNORE_SUFFIXES = {".meta", ".pyc", ".pyo"}


def _parse_gitignore(gitignore_path: Path) -> list[re.Pattern[str]]:
    """解析 .gitignore 文件为正则表达式列表

    支持的语法:
    - 普通目录/文件名: build/  *.log
    - 通配符: *.js  temp*
    - 前缀斜杠: /dist (仅匹配根目录下)
    - 否定模式: !important.log (不处理, 跳过)
    - 注释和空行: # comment

    Args:
        gitignore_path: .gitignore 文件路径

    Returns:
        编译后的正则表达式列表
    """
    if not gitignore_path.is_file():
        return []

    patterns: list[re.Pattern[str]] = []
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    for line in lines:
        line = line.strip()
        # 跳过空行、注释、否定模式
        if not line or line.startswith("#") or line.startswith("!"):
            continue

        # 去掉尾部斜杠(目录标记)和前导斜杠(根目录标记)
        pattern = line.rstrip("/").lstrip("/")
        if not pattern:
            continue

        # 将 gitignore glob 转为正则:
        # ** → 匹配任意路径段
        # *  → 匹配非斜杠字符
        # ?  → 匹配单个非斜杠字符
        # .  → 转义
        regex = ""
        i = 0
        while i < len(pattern):
            c = pattern[i]
            if c == "*":
                if i + 1 < len(pattern) and pattern[i + 1] == "*":
                    regex += ".*"
                    i += 2
                    if i < len(pattern) and pattern[i] == "/":
                        i += 1  # 跳过 **/  后面的 /
                    continue
                else:
                    regex += "[^/]*"
            elif c == "?":
                regex += "[^/]"
            elif c == ".":
                regex += r"\."
            else:
                regex += re.escape(c)
            i += 1

        try:
            patterns.append(re.compile(regex))
        except re.error:
            continue

    return patterns


def _is_gitignored(
    entry_name: str,
    rel_path: str,
    gitignore_patterns: list[re.Pattern[str]],
) -> bool:
    """检查路径是否被 gitignore 规则匹配

    Args:
        entry_name: 文件/目录名
        rel_path: 相对于项目根目录的路径 (使用 / 分隔)
        gitignore_patterns: 编译后的 gitignore 正则列表

    Returns:
        True 表示应被忽略
    """
    for pat in gitignore_patterns:
        # 匹配完整相对路径或仅文件名/目录名
        if pat.fullmatch(entry_name) or pat.fullmatch(rel_path):
            return True
        # 也匹配路径中的任意段
        if pat.search(rel_path):
            return True
    return False


def _find_gitignore(start_dir: Path) -> list[re.Pattern[str]]:
    """从目标目录向上查找最近的 .gitignore 并解析

    Args:
        start_dir: 起始目录

    Returns:
        编译后的 gitignore 正则列表
    """
    current = start_dir.resolve()
    for _ in range(20):  # 最多向上查找 20 级
        gitignore = current / ".gitignore"
        if gitignore.is_file():
            return _parse_gitignore(gitignore)
        # 如果找到 .git 目录说明是项目根，停止
        if (current / ".git").is_dir():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return []


def list_directory_tool(
    path: str,
    max_depth: int = 3,
    include_files: bool = True,
    ignore_dirs: list[str] | None = None,
) -> str:
    """列出目录树结构

    自动读取 .gitignore 规则，忽略被 git 排除的目录和文件。

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

    # 解析 .gitignore
    gitignore_patterns = _find_gitignore(root)

    lines: list[str] = [f"{root.name}/"]
    _walk(root, root, lines, depth=1, max_depth=max_depth,
          include_files=include_files, skip_dirs=skip_dirs,
          gitignore_patterns=gitignore_patterns)

    return "\n".join(lines)


def _walk(
    directory: Path,
    root: Path,
    lines: list[str],
    depth: int,
    max_depth: int,
    include_files: bool,
    skip_dirs: set[str],
    gitignore_patterns: list[re.Pattern[str]],
) -> None:
    """递归构建目录树

    Args:
        directory: 当前遍历的目录
        root: 项目根目录 (用于计算相对路径)
        lines: 输出行列表
        depth: 当前深度
        max_depth: 最大递归深度
        include_files: 是否包含文件
        skip_dirs: 硬编码忽略的目录名集合
        gitignore_patterns: .gitignore 编译后的正则列表
    """
    if depth > max_depth:
        return

    indent = "  " * depth
    try:
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return

    for entry in entries:
        if entry.name.startswith(".") and entry.name not in (".gitkeep",):
            continue

        # 计算相对路径用于 gitignore 匹配
        try:
            rel = entry.relative_to(root).as_posix()
        except ValueError:
            rel = entry.name

        if entry.is_dir():
            if entry.name in skip_dirs:
                continue
            if gitignore_patterns and _is_gitignored(entry.name, rel, gitignore_patterns):
                continue
            lines.append(f"{indent}{entry.name}/")
            _walk(entry, root, lines, depth + 1, max_depth, include_files,
                  skip_dirs, gitignore_patterns)
        elif include_files:
            if entry.suffix in _IGNORE_SUFFIXES:
                continue
            if gitignore_patterns and _is_gitignored(entry.name, rel, gitignore_patterns):
                continue
            lines.append(f"{indent}{entry.name}")


# LLM tool_use 工具定义
LIST_DIRECTORY_TOOL_DEFINITION = {
    "name": "list_directory",
    "description": (
        "列出目录的树形结构，包含子目录和文件。"
        "自动读取 .gitignore 规则，忽略被 git 排除的文件和目录。"
        "同时忽略 node_modules/.git 等无关目录。"
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
