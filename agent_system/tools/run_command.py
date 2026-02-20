"""命令执行工具 — subprocess 封装（Windows 进程树安全）

支持两种模式:
1. 普通模式: 命令执行完毕后返回（默认）
2. 交互模式: 检测到进程空闲时提前返回 process_id，LLM 可通过 send_stdin 继续交互
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent_system.tools.process import (
    kill_process_tree,
    run_process,
    start_interactive_process,
    get_interactive_process,
    remove_interactive_process,
)

# 保持后向兼容: ts_check 等旧代码可能 import 这个
_kill_process_tree = kill_process_tree


@dataclass
class CommandResult:
    """命令执行结果"""
    stdout: str
    stderr: str
    exit_code: int
    process_id: str | None = None  # 交互模式下进程仍在运行时返回

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def run_command_tool(
    command: str,
    cwd: str | None = None,
    timeout: int = 0,
    stdin_input: str | None = None,
    interactive: bool = False,
    idle_timeout: float = 10.0,
) -> CommandResult:
    """执行 shell 命令

    普通模式（interactive=False）:
        命令执行完毕后返回。如提供 stdin_input 会在启动时写入 stdin。

    交互模式（interactive=True）:
        启动进程后持续读输出。如果进程在 idle_timeout 秒内无新输出且未退出，
        认为进程在等待输入，提前返回 process_id。LLM 可用 send_stdin_tool 继续交互。

    Args:
        command: 要执行的命令字符串
        cwd: 工作目录（可选）
        timeout: 超时秒数（0 = 无限制，仅普通模式生效）
        stdin_input: 要写入进程 stdin 的文本（仅普通模式）
        interactive: 是否使用交互模式
        idle_timeout: 交互模式下无输出时的等待秒数（默认 10s）

    Returns:
        CommandResult 包含 stdout/stderr/exit_code，交互模式下可能包含 process_id
    """
    if not interactive:
        # 普通模式: 一次性执行
        result = run_process(
            cmd=command,
            cwd=cwd,
            timeout=timeout,
            heartbeat_interval=15,
            stream_output=True,
            log_prefix="[cmd] ",
            stdin_input=stdin_input,
        )
        return CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )

    # 交互模式
    ip = start_interactive_process(cmd=command, cwd=cwd)
    output = ip.read_output(idle_timeout=idle_timeout)

    if not output.running:
        # 进程已经执行完毕，清理并返回
        remove_interactive_process(ip.process_id)
        return CommandResult(
            stdout=output.stdout,
            stderr=output.stderr,
            exit_code=output.returncode if output.returncode is not None else -1,
        )

    # 进程仍在运行（可能在等待输入）
    return CommandResult(
        stdout=output.stdout,
        stderr=output.stderr,
        exit_code=-1,  # 尚未退出
        process_id=output.process_id,
    )


def send_stdin_tool(
    process_id: str,
    input_text: str,
    idle_timeout: float = 10.0,
) -> CommandResult:
    """向正在运行的交互式进程发送输入，然后继续读取输出

    Args:
        process_id: 由 run_command（交互模式）返回的进程 ID
        input_text: 要写入 stdin 的文本
        idle_timeout: 发送后等待新输出的超时秒数

    Returns:
        CommandResult 包含新输出和进程状态
    """
    ip = get_interactive_process(process_id)
    if ip is None:
        return CommandResult(
            stdout="",
            stderr=f"进程 {process_id} 不存在或已结束",
            exit_code=-1,
        )

    if not ip.send_input(input_text):
        remove_interactive_process(process_id)
        return CommandResult(
            stdout="",
            stderr=f"进程 {process_id} 已退出，无法写入",
            exit_code=ip.proc.returncode if ip.proc.returncode is not None else -1,
        )

    output = ip.read_output(idle_timeout=idle_timeout)

    if not output.running:
        remove_interactive_process(process_id)
        return CommandResult(
            stdout=output.stdout,
            stderr=output.stderr,
            exit_code=output.returncode if output.returncode is not None else -1,
        )

    return CommandResult(
        stdout=output.stdout,
        stderr=output.stderr,
        exit_code=-1,
        process_id=process_id,
    )


# ---------------------------------------------------------------------------
# LLM tool_use 工具定义
# ---------------------------------------------------------------------------

RUN_COMMAND_TOOL_DEFINITION = {
    "name": "run_command",
    "description": (
        "执行 shell 命令。返回 stdout、stderr 和 exit_code。"
        "设置 interactive=true 可启用交互模式：当进程等待输入时会提前返回 process_id，"
        "之后可用 send_stdin 工具向进程发送输入。"
    ),
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
                "description": "超时秒数，0 = 无限制（默认，仅非交互模式）",
                "default": 0,
            },
            "stdin_input": {
                "type": "string",
                "description": "启动时写入 stdin 的文本（仅非交互模式，如已知需要输入 Y/N）",
            },
            "interactive": {
                "type": "boolean",
                "description": "是否启用交互模式。启用后命令在等待输入时会提前返回 process_id",
                "default": False,
            },
            "idle_timeout": {
                "type": "number",
                "description": "交互模式下，无新输出时等待的秒数（默认 10）",
                "default": 10,
            },
        },
        "required": ["command"],
    },
}

SEND_STDIN_TOOL_DEFINITION = {
    "name": "send_stdin",
    "description": (
        "向正在运行的交互式进程发送输入。"
        "process_id 由 run_command（interactive=true）返回。"
        "发送后会继续读取输出直到进程退出或再次空闲。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "process_id": {
                "type": "string",
                "description": "由 run_command 交互模式返回的进程 ID",
            },
            "input_text": {
                "type": "string",
                "description": "要发送给进程的文本（如 'Y\\n' 表示确认）",
            },
            "idle_timeout": {
                "type": "number",
                "description": "发送后等待新输出的超时秒数（默认 10）",
                "default": 10,
            },
        },
        "required": ["process_id", "input_text"],
    },
}
