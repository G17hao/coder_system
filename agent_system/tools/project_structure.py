"""项目结构摘要工具 — 一次性返回项目关键文件和结构"""

from __future__ import annotations

import json
from pathlib import Path

# 关注的源代码文件后缀
_SOURCE_SUFFIXES = {".ts", ".lua", ".js", ".json"}

# 忽略的目录
_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build",
    "library", "temp", ".pytest_cache", "profiles", "remote",
    "external_assets", "external_assets_1024",
}


def get_project_structure_tool(
    project_root: str,
    source_dirs: list[str] | None = None,
    extensions: list[str] | None = None,
    max_files: int = 500,
) -> str:
    """生成项目结构摘要

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

    # 确定扫描目标
    scan_roots: list[Path] = []
    if source_dirs:
        for sd in source_dirs:
            candidate = root / sd
            if candidate.is_dir():
                scan_roots.append(candidate)
    else:
        scan_roots = [root]

    # 收集文件
    files_by_dir: dict[str, list[str]] = {}
    total = 0

    for scan_root in scan_roots:
        for fp in sorted(scan_root.rglob("*")):
            if not fp.is_file():
                continue
            if fp.suffix not in exts:
                continue

            # 检查是否在忽略目录中
            parts = fp.relative_to(root).parts
            if any(part in _IGNORE_DIRS for part in parts):
                continue

            rel_dir = str(fp.parent.relative_to(root)).replace("\\", "/")
            rel_file = fp.name

            if rel_dir not in files_by_dir:
                files_by_dir[rel_dir] = []
            files_by_dir[rel_dir].append(rel_file)

            total += 1
            if total >= max_files:
                break

        if total >= max_files:
            break

    # 提取 TypeScript 类/接口摘要（轻量扫描）
    exports: list[dict[str, str]] = []
    for scan_root in scan_roots:
        for fp in sorted(scan_root.rglob("*.ts")):
            if not fp.is_file():
                continue
            parts = fp.relative_to(root).parts
            if any(part in _IGNORE_DIRS for part in parts):
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
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
                        "file": str(fp.relative_to(root)).replace("\\", "/"),
                        "declaration": name[:120],
                    })

            if len(exports) >= 200:
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
