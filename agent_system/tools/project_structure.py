"""项目结构摘要工具 — 一次性返回项目关键文件和结构，支持 .gitignore"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent_system.tools.list_directory import (
    _IGNORE_DIRS,
    _IGNORE_SUFFIXES,
    _find_gitignore,
    _is_gitignored,
)

# 关注的源代码文件后缀
_SOURCE_SUFFIXES = {".ts", ".lua", ".js", ".json"}


def get_project_structure_tool(
    project_root: str,
    source_dirs: list[str] | None = None,
    extensions: list[str] | None = None,
    max_files: int = 500,
) -> str:
    """生成项目结构摘要

    自动遵循 .gitignore 规则跳过被忽略的目录和文件。
    单次遍历同时收集文件列表和 TypeScript export 声明。

    Args:
        project_root: 项目根目录
        source_dirs: 限定扫描的子目录列表（相对路径），None 扫描全部
        extensions: 关注的文件后缀列表（如 [".ts"]），None 使用默认
        max_files: 最大文件数量限制

    Returns:
        JSON 格式的项目结构摘要
    """
    root = Path(project_root)
    if not root.is_dir():
        return json.dumps({"error": f"目录不存在: {project_root}"}, ensure_ascii=False)

    exts = set(extensions) if extensions else _SOURCE_SUFFIXES

    # 解析 gitignore
    skip_dirs = set(_IGNORE_DIRS)
    gitignore_patterns = _find_gitignore(root)

    # 确定扫描目标
    scan_roots: list[Path] = []
    if source_dirs:
        for sd in source_dirs:
            candidate = root / sd
            if candidate.is_dir():
                scan_roots.append(candidate)
    else:
        scan_roots = [root]

    # 单次遍历同时收集文件和 exports
    files_by_dir: dict[str, list[str]] = {}
    exports: list[dict[str, str]] = []
    total = 0

    for scan_root in scan_roots:
        _structure_walk(
            scan_root, root, exts,
            skip_dirs, gitignore_patterns,
            files_by_dir, exports,
            counter=[total], max_files=max_files,
        )
        total = sum(len(v) for v in files_by_dir.values())
        if total >= max_files:
            break

    result = {
        "project_root": str(root),
        "total_source_files": total,
        "directories": {
            k: files_by_dir[k]
            for k in sorted(files_by_dir.keys())
        },
        "exports": exports[:200],
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def _structure_walk(
    directory: Path,
    root: Path,
    exts: set[str],
    skip_dirs: set[str],
    gitignore_patterns: list[re.Pattern[str]],
    files_by_dir: dict[str, list[str]],
    exports: list[dict[str, str]],
    counter: list[int],
    max_files: int,
) -> None:
    """递归遍历目录，同时收集文件列表和 TS export 声明"""
    if counter[0] >= max_files:
        return

    try:
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return

    for entry in entries:
        if counter[0] >= max_files:
            return

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
            _structure_walk(
                entry, root, exts,
                skip_dirs, gitignore_patterns,
                files_by_dir, exports,
                counter, max_files,
            )
        elif entry.is_file():
            if entry.suffix in _IGNORE_SUFFIXES:
                continue
            if gitignore_patterns and _is_gitignored(entry.name, rel, gitignore_patterns):
                continue
            if entry.suffix not in exts:
                continue

            rel_dir = str(entry.parent.relative_to(root)).replace("\\", "/")
            if rel_dir not in files_by_dir:
                files_by_dir[rel_dir] = []
            files_by_dir[rel_dir].append(entry.name)
            counter[0] += 1

            # 顺便提取 TS export 声明
            if entry.suffix == ".ts" and len(exports) < 200:
                try:
                    content = entry.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("export class ") or \
                       stripped.startswith("export interface ") or \
                       stripped.startswith("export enum ") or \
                       stripped.startswith("export abstract class "):
                        name = stripped.split("{")[0].strip() if "{" in stripped else stripped
                        exports.append({
                            "file": rel,
                            "declaration": name[:120],
                        })


# LLM tool_use 工具定义
GET_PROJECT_STRUCTURE_TOOL_DEFINITION = {
    "name": "get_project_structure",
    "description": (
        "一次性获取项目的文件结构摘要，包括按目录分组的源文件列表和 TypeScript export 声明。"
        "比多次调用 list_directory + read_file 更高效。"
        "适合在分析任务开始时调用，快速了解项目全貌。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_root": {
                "type": "string",
                "description": "项目根目录绝对路径",
            },
            "source_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "限定扫描的子目录（相对路径），不指定则扫描全部",
            },
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关注的文件后缀（如 [\".ts\"]），不指定则使用默认集",
            },
        },
        "required": ["project_root"],
    },
}
