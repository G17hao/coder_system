"""文件搜索工具 — glob/regex 搜索，优先使用 git ls-files 加速"""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
import time
from pathlib import Path

from agent_system.tools.list_directory import (
    _IGNORE_DIRS,
    _IGNORE_SUFFIXES,
    _find_gitignore,
    _is_gitignored,
)

logger = logging.getLogger(__name__)

# 默认最大返回数
_DEFAULT_MAX_RESULTS = 200


def _find_git_root(start: Path) -> Path | None:
    """向上查找 .git 目录，返回 git 仓库根目录"""
    current = start.resolve()
    for _ in range(20):
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _search_via_git(
    base_dir: Path,
    git_root: Path,
    pattern: str,
    compiled_re: re.Pattern[str] | None,
    max_results: int,
) -> list[str] | None:
    """使用 git ls-files 快速搜索（自动遵循 .gitignore）

    Returns:
        匹配的绝对路径列表, 如果 git 命令失败则返回 None（触发 fallback）
    """
    try:
        start = time.time()
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        elapsed = time.time() - start
        all_files = proc.stdout.splitlines()
        logger.info(f"    [search] git ls-files: {len(all_files)} 个文件 ({elapsed:.1f}s)")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    # base_dir 相对 git_root 的前缀（限定搜索范围）
    try:
        base_rel = base_dir.resolve().relative_to(git_root.resolve()).as_posix()
        if base_rel == ".":
            base_rel = ""
    except ValueError:
        base_rel = ""

    results: list[str] = []
    for rel_path in all_files:
        # 限定在 base_dir 下
        if base_rel and not rel_path.startswith(base_rel + "/") and rel_path != base_rel:
            continue

        filename = rel_path.rsplit("/", 1)[-1]

        # glob 模式匹配文件名
        if not fnmatch.fnmatch(filename, pattern):
            continue

        abs_path = str(git_root / rel_path)

        # 正则过滤
        if compiled_re and not compiled_re.search(abs_path):
            continue

        results.append(abs_path)
        if len(results) >= max_results:
            break

    return results


def search_file_tool(
    base_dir: str,
    pattern: str = "*",
    regex: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
    respect_gitignore: bool = True,
) -> list[str]:
    """搜索匹配模式的文件

    优先使用 git ls-files 快速搜索（自动遵循 .gitignore）。
    如不在 git 仓库内或 git 命令失败，回退到文件系统遍历。
    设置 respect_gitignore=False 强制使用文件系统遍历。

    Args:
        base_dir: 搜索根目录
        pattern: glob 模式（如 "*.lua", "**/*.ts"）
        regex: 可选的正则表达式过滤（匹配文件路径）
        max_results: 最大返回结果数（默认 200）
        respect_gitignore: 是否遵循 .gitignore 规则（默认 True）

    Returns:
        匹配的文件路径列表（绝对路径字符串）
    """
    start = time.time()
    base = Path(base_dir)
    if not base.is_dir():
        return []

    compiled_re = re.compile(regex) if regex else None

    # 快速路径: 使用 git ls-files
    if respect_gitignore:
        git_root = _find_git_root(base)
        if git_root is not None:
            results = _search_via_git(base, git_root, pattern, compiled_re, max_results)
            if results is not None:
                elapsed = time.time() - start
                logger.info(f"    [search] 完成: {len(results)} 个匹配 ({elapsed:.1f}s)")
                return sorted(results)
            logger.info("    [search] git ls-files 失败，回退到文件系统遍历")

    # 回退路径: 文件系统遍历
    logger.info(f"    [search] 文件系统遍历 base={base_dir} pattern={pattern}")
    if respect_gitignore:
        skip_dirs = set(_IGNORE_DIRS)
        gitignore_patterns = _find_gitignore(base)
    else:
        skip_dirs = set()
        gitignore_patterns = []

    results_list: list[str] = []
    dirs_scanned = 0
    _search_walk(
        base, base, pattern, compiled_re,
        skip_dirs, gitignore_patterns,
        results_list, max_results, respect_gitignore,
        _counter=[0],  # mutable counter for dir progress
    )

    elapsed = time.time() - start
    logger.info(f"    [search] 完成: {len(results_list)} 个匹配 ({elapsed:.1f}s)")
    return sorted(results_list)


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
    _counter: list[int] | None = None,
) -> None:
    """递归搜索目录，跳过忽略的路径"""
    if len(results) >= max_results:
        return

    # 进度日志
    if _counter is not None:
        _counter[0] += 1
        if _counter[0] % 100 == 0:
            logger.info(f"    [search] 已扫描 {_counter[0]} 个目录, 已找到 {len(results)} 个匹配...")

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
                _counter,
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
