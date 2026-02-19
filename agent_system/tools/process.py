"""子进程执行器 — 带实时输出流和心跳日志（Windows 进程树安全）"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def kill_process_tree(proc: subprocess.Popen) -> None:
    """杀掉进程及其所有子进程（Windows 兼容）"""
    try:
        if sys.platform == "win32":
            subprocess.run(
                f"taskkill /F /T /PID {proc.pid}",
                shell=True,
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


@dataclass
class ProcessResult:
    """进程执行结果"""
    stdout: str
    stderr: str
    returncode: int
    elapsed: float
    timed_out: bool = False


def run_process(
    cmd: str,
    cwd: str | None = None,
    timeout: int = 0,
    heartbeat_interval: int = 15,
    stream_output: bool = True,
    log_prefix: str = "[cmd] ",
) -> ProcessResult:
    """执行子进程，带实时输出流和心跳日志

    - 实时逐行转发 stdout/stderr 到日志
    - 每 heartbeat_interval 秒输出一次心跳（如果进程仍在运行）
    - timeout > 0 时超时自动杀掉整个进程树（Windows 安全）
    - timeout = 0 时无超时限制，进程运行到自然结束

    Args:
        cmd: 命令字符串
        cwd: 工作目录
        timeout: 超时秒数（0 = 无限制）
        heartbeat_interval: 心跳日志间隔秒数（0 禁用）
        stream_output: 是否实时流式输出命令的 stdout/stderr
        log_prefix: 日志前缀

    Returns:
        ProcessResult 包含 stdout/stderr/returncode/elapsed/timed_out
    """
    logger.info(f"    {log_prefix}执行: {cmd}")
    start_time = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"    {log_prefix}启动失败: {e}")
        return ProcessResult(
            stdout="",
            stderr=f"命令启动失败: {e}",
            returncode=-1,
            elapsed=elapsed,
        )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _read_stream(
        stream: object,
        lines: list[str],
        is_stderr: bool = False,
    ) -> None:
        """逐行读取流并记录日志"""
        for line in stream:  # type: ignore[union-attr]
            lines.append(line)
            if stream_output:
                stripped = line.rstrip()
                if stripped:
                    tag = f"{log_prefix}[stderr] " if is_stderr else log_prefix
                    logger.info(f"    {tag}{stripped}")

    t_out = threading.Thread(
        target=_read_stream, args=(proc.stdout, stdout_lines, False),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_read_stream, args=(proc.stderr, stderr_lines, True),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    # 等待完成，期间输出心跳；timeout > 0 时有超时保护
    hb = heartbeat_interval if heartbeat_interval > 0 else 30
    while True:
        if timeout > 0:
            remaining = timeout - (time.time() - start_time)
            if remaining <= 0:
                wait_time = 0.1
            else:
                wait_time = min(hb, remaining)
        else:
            wait_time = hb

        t_out.join(timeout=max(wait_time, 0.1))
        elapsed = time.time() - start_time

        if not t_out.is_alive():
            # stdout 读完 → 进程已结束
            break

        if timeout > 0 and elapsed >= timeout:
            logger.warning(
                f"    {log_prefix}超时 ({timeout}s)，正在终止进程树..."
            )
            kill_process_tree(proc)
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            return ProcessResult(
                stdout="".join(stdout_lines),
                stderr=f"命令超时 ({timeout}s): {cmd}",
                returncode=-1,
                elapsed=elapsed,
                timed_out=True,
            )

        # 心跳
        logger.info(
            f"    {log_prefix}仍在执行... (已运行 {int(elapsed)}s)"
        )

    # 等待 stderr 线程也结束
    t_err.join(timeout=10)
    proc.wait()
    elapsed = time.time() - start_time

    logger.info(
        f"    {log_prefix}完成 (耗时 {elapsed:.1f}s, exit={proc.returncode})"
    )

    return ProcessResult(
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        returncode=proc.returncode,
        elapsed=elapsed,
    )
