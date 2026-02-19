"""命令执行工具 — subprocess 封装（Windows 进程树安全）"""

from __future__ import annotations

from dataclasses import dataclass

from agent_system.tools.process import kill_process_tree, run_process

# 保持后向兼容: ts_check 等旧代码可能 import 这个
_kill_process_tree = kill_process_tree


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
    """执行 shell 命令，带实时输出流和心跳日志

    Args:
        command: 要执行的命令字符串
        cwd: 工作目录（可选）
        timeout: 超时秒数

    Returns:
        CommandResult 包含 stdout/stderr/exit_code
    """
    result = run_process(
        cmd=command,
        cwd=cwd,
        timeout=timeout,
        heartbeat_interval=15,
        stream_output=True,
        log_prefix="[cmd] ",
    )
    return CommandResult(
        stdout=result.stdout,
        stderr=result.stderr if not result.timed_out else result.stderr,
        exit_code=result.returncode,
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
