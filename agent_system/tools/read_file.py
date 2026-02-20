"""文件读取工具 — LLM tool_use 可调用"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def read_files_tool(
    *,
    paths: list[str] | None = None,
    requests: list[dict[str, Any]] | None = None,
    start: int = 1,
    end: int | None = None,
) -> str:
    """批量读取多个文件内容。

    支持两种输入形式：
    1) paths: ["a.ts", "b.ts"]，复用同一 start/end
    2) requests: [{"path":"a.ts","start":1,"end":20}, ...]

    Returns:
        JSON 字符串：{"files": [{"path":..., "content":..., "error":...}, ...]}
    """
    normalized_requests: list[dict[str, Any]] = []

    if requests:
        for item in requests:
            req_path = str(item.get("path", "")).strip()
            if not req_path:
                continue
            normalized_requests.append({
                "path": req_path,
                "start": int(item.get("start", 1)),
                "end": item.get("end"),
            })
    elif paths:
        for req_path in paths:
            path_text = str(req_path).strip()
            if not path_text:
                continue
            normalized_requests.append({
                "path": path_text,
                "start": start,
                "end": end,
            })

    results: list[dict[str, Any]] = []
    for req in normalized_requests:
        file_path = req["path"]
        file_start = req["start"]
        file_end = req["end"]
        try:
            content = read_file_tool(
                path=file_path,
                start=file_start,
                end=file_end,
            )
            results.append({
                "path": file_path,
                "start": file_start,
                "end": file_end,
                "content": content,
            })
        except FileNotFoundError as e:
            results.append({
                "path": file_path,
                "start": file_start,
                "end": file_end,
                "error": str(e),
            })

    return json.dumps({"files": results}, ensure_ascii=False)


# LLM tool_use 工具定义
READ_FILE_TOOL_DEFINITION = {
    "name": "read_file",
    "description": "读取单个或多个文件内容。支持 path（单文件）或 paths/requests（批量）。",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（绝对路径或相对于项目根目录）",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "批量读取的文件路径列表（共享 start/end）",
            },
            "requests": {
                "type": "array",
                "description": "批量读取请求列表（每个文件可单独指定 start/end）",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start": {"type": "integer", "default": 1},
                        "end": {"type": "integer"},
                    },
                    "required": ["path"],
                },
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
        "anyOf": [
            {"required": ["path"]},
            {"required": ["paths"]},
            {"required": ["requests"]},
        ],
    },
}
