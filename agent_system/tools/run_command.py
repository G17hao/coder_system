"""命令执行工具 — subprocess 封装（Windows 进程树安全）"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
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


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀掉进程及其所有子进程（Windows 兼容）"""
    try:
        if sys.platform == "win32":
            # Windows: taskkill /T 杀进程树
            subprocess.run(
                f"taskkill /F /T /PID {proc.pid}",
                shell=True,
                capture_output=True,
                timeout=10,
            )
        else:
            # Unix: 杀进程组
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        # 最后手段
        try:
            proc.kill()
        except Exception:
            pass


def run_command_tool(
    command: str,
    cwd: str | None = None,
    timeout: int = 60,
) -> CommandResult:
    """执行 shell 命令

    使用 Popen 手动管理超时和进程树清理，
    避免 subprocess.run 在 Windows 上超时后挂起。

    Args:
        command: 要执行的命令字符串
        cwd: 工作目录（可选）
        timeout: 超时秒数

    Returns:
        CommandResult 包含 stdout/stderr/exit_code
    """
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return CommandResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            # 杀掉整个进程树，而非仅顶层进程
            _kill_process_tree(proc)
            # 等待管道关闭（最多 5 秒）
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, Exception):
                stdout, stderr = "", ""
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
