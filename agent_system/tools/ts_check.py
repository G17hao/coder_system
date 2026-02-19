"""TypeScript 编译检查工具 — 结构化解析 tsc 输出（Windows 进程树安全）"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from agent_system.tools.run_command import _kill_process_tree


@dataclass
class TsError:
    """单个 TypeScript 编译错误"""
    file: str
    line: int
    column: int
    code: str
    message: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class TsCheckResult:
    """TypeScript 检查结果"""
    success: bool
    error_count: int = 0
    errors: list[TsError] = field(default_factory=list)
    raw_output: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error_count": self.error_count,
            "errors": [e.to_dict() for e in self.errors[:50]],  # 最多返回 50 个
            "raw_output": self.raw_output[:3000],  # 截断原始输出
        }


# tsc 错误行格式: path(line,col): error TSxxxx: message
_TSC_ERROR_RE = re.compile(
    r"^(.+?)\((\d+),(\d+)\):\s+error\s+(TS\d+):\s+(.+)$"
)


def ts_check_tool(
    project_root: str,
    tsconfig: str = "tsconfig.json",
    timeout: int = 120,
) -> TsCheckResult:
    """执行 TypeScript 编译检查并解析错误

    Args:
        project_root: 项目根目录
        tsconfig: tsconfig 文件名
        timeout: 超时秒数

    Returns:
        TsCheckResult 结构化结果
    """
    cmd = f"npx tsc --noEmit --project {tsconfig}"

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return TsCheckResult(
                success=False,
                raw_output=f"tsc 超时 ({timeout}s)",
            )

        returncode = proc.returncode
    except Exception as e:
        return TsCheckResult(
            success=False,
            raw_output=f"执行失败: {e}",
        )

    raw = stdout + stderr
    errors: list[TsError] = []

    for line in raw.splitlines():
        m = _TSC_ERROR_RE.match(line.strip())
        if m:
            errors.append(TsError(
                file=m.group(1),
                line=int(m.group(2)),
                column=int(m.group(3)),
                code=m.group(4),
                message=m.group(5),
            ))

    return TsCheckResult(
        success=returncode == 0,
        error_count=len(errors),
        errors=errors,
        raw_output=raw,
    )


# LLM tool_use 工具定义
TS_CHECK_TOOL_DEFINITION = {
    "name": "ts_check",
    "description": (
        "执行 TypeScript 编译检查（npx tsc --noEmit），"
        "返回结构化的错误列表（文件、行号、列号、错误码、消息），"
        "比 run_command 更方便——自动解析 tsc 输出。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_root": {
                "type": "string",
                "description": "项目根目录绝对路径",
            },
            "tsconfig": {
                "type": "string",
                "description": "tsconfig 文件名，默认 'tsconfig.json'",
                "default": "tsconfig.json",
            },
        },
        "required": ["project_root"],
    },
}
