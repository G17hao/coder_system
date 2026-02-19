"""命令执行工具 — subprocess 封装"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    """命令执行结果"""
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def run_command_tool(
    command: str,
    cwd: str | None = None,
    timeout: int = 60,
) -> CommandResult:
    """执行 shell 命令

    Args:
        command: 要执行的命令字符串
        cwd: 工作目录（可选）
        timeout: 超时秒数

    Returns:
        CommandResult 包含 stdout/stderr/exit_code
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            stdout="",
            stderr=f"命令超时 ({timeout}s): {command}",
            exit_code=-1,
        )
    except Exception as e:
        return CommandResult(
            stdout="",
            stderr=f"命令执行失败: {e}",
            exit_code=-1,
        )


# LLM tool_use 工具定义
RUN_COMMAND_TOOL_DEFINITION = {
    "name": "run_command",
    "description": "执行 shell 命令。返回 stdout、stderr 和 exit_code。",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录（可选）",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 60",
                "default": 60,
            },
        },
        "required": ["command"],
    },
}
