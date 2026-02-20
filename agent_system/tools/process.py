"""子进程执行器 — 带实时输出流和心跳日志（Windows 进程树安全）

支持两种模式:
1. 一次性执行 (run_process): 启动 → 等完成 → 返回
2. 交互式执行 (InteractiveProcess): 启动 → 读输出 → 写入 stdin → 继续读
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

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
    stdin_input: str | None = None,
) -> ProcessResult:
    """执行子进程，带实时输出流和心跳日志

    - 实时逐行转发 stdout/stderr 到日志
    - 每 heartbeat_interval 秒输出一次心跳（如果进程仍在运行）
    - timeout > 0 时超时自动杀掉整个进程树（Windows 安全）
    - timeout = 0 时无超时限制，进程运行到自然结束
    - stdin_input 不为 None 时，将内容写入进程 stdin 后关闭

    Args:
        cmd: 命令字符串
        cwd: 工作目录
        timeout: 超时秒数（0 = 无限制）
        heartbeat_interval: 心跳日志间隔秒数（0 禁用）
        stream_output: 是否实时流式输出命令的 stdout/stderr
        log_prefix: 日志前缀
        stdin_input: 要写入进程 stdin 的文本（可选）

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
            stdin=subprocess.PIPE if stdin_input is not None else subprocess.DEVNULL,
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

    # 如果有 stdin_input，先写入再关闭
    if stdin_input is not None and proc.stdin:
        try:
            proc.stdin.write(stdin_input)
            proc.stdin.close()
        except Exception:
            pass

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


# ---------------------------------------------------------------------------
# 交互式进程 — 保持进程存活，支持 LLM 多轮读写
# ---------------------------------------------------------------------------

@dataclass
class InteractiveOutput:
    """交互式进程的一次读取结果"""
    stdout: str
    stderr: str
    process_id: str
    running: bool          # 进程是否仍在运行
    returncode: int | None  # 进程退出码（仍在运行时为 None）
    elapsed: float


class InteractiveProcess:
    """管理一个可交互的子进程

    启动时 stdin=PIPE，stdout/stderr 由后台线程持续读取。
    调用方可以随时:
    - read_output(idle_timeout): 读取当前缓冲的输出，如果进程空闲超过 idle_timeout 秒则返回
    - send_input(text): 向进程 stdin 写入文本
    - kill(): 终止进程
    """

    def __init__(self, proc: subprocess.Popen, process_id: str) -> None:
        self.proc = proc
        self.process_id = process_id
        self._start_time = time.time()
        self._stdout_buf: list[str] = []
        self._stderr_buf: list[str] = []
        self._lock = threading.Lock()
        self._last_output_time = time.time()
        self._finished = threading.Event()

        # 后台线程持续读 stdout/stderr
        self._t_out = threading.Thread(
            target=self._read_stream, args=(proc.stdout, self._stdout_buf, False),
            daemon=True,
        )
        self._t_err = threading.Thread(
            target=self._read_stream, args=(proc.stderr, self._stderr_buf, True),
            daemon=True,
        )
        self._t_out.start()
        self._t_err.start()

    def _read_stream(
        self,
        stream: object,
        buf: list[str],
        is_stderr: bool,
    ) -> None:
        """逐行读取流，追加到缓冲区"""
        try:
            for line in stream:  # type: ignore[union-attr]
                with self._lock:
                    buf.append(line)
                    self._last_output_time = time.time()
                tag = "[stderr] " if is_stderr else ""
                stripped = line.rstrip()  # type: ignore[union-attr]
                if stripped:
                    logger.info(f"    [interactive] {tag}{stripped}")
        except Exception:
            pass
        finally:
            if not is_stderr:
                # stdout 结束 → 进程已退出
                self._finished.set()

    def read_output(self, idle_timeout: float = 10.0) -> InteractiveOutput:
        """读取缓冲输出，如果进程空闲超过 idle_timeout 秒则提前返回

        Args:
            idle_timeout: 无新输出的最大等待秒数

        Returns:
            InteractiveOutput 包含当前缓冲输出和进程状态
        """
        # 等待进程结束或空闲超时
        while True:
            if self._finished.wait(timeout=1.0):
                # 进程已结束，稍等确保所有输出都读完
                self._t_out.join(timeout=2)
                self._t_err.join(timeout=2)
                self.proc.wait()
                break

            with self._lock:
                idle_secs = time.time() - self._last_output_time

            if idle_secs >= idle_timeout:
                logger.info(
                    f"    [interactive] 进程空闲 {idle_secs:.0f}s，可能在等待输入"
                )
                break

        # 取出缓冲区内容并清空
        with self._lock:
            stdout = "".join(self._stdout_buf)
            stderr = "".join(self._stderr_buf)
            self._stdout_buf.clear()
            self._stderr_buf.clear()

        running = self.proc.poll() is None
        returncode = self.proc.returncode

        return InteractiveOutput(
            stdout=stdout,
            stderr=stderr,
            process_id=self.process_id,
            running=running,
            returncode=returncode,
            elapsed=time.time() - self._start_time,
        )

    def send_input(self, text: str) -> bool:
        """向进程 stdin 写入文本

        Args:
            text: 要写入的文本

        Returns:
            是否写入成功
        """
        if self.proc.poll() is not None:
            logger.warning("    [interactive] 进程已退出，无法写入 stdin")
            return False

        try:
            if self.proc.stdin:
                self.proc.stdin.write(text)
                self.proc.stdin.flush()
                logger.info(f"    [interactive] 写入 stdin: {text.rstrip()}")
                with self._lock:
                    self._last_output_time = time.time()
                return True
        except Exception as e:
            logger.warning(f"    [interactive] 写入 stdin 失败: {e}")
        return False

    def kill(self) -> None:
        """终止进程"""
        kill_process_tree(self.proc)
        self._finished.set()

    @property
    def is_running(self) -> bool:
        return self.proc.poll() is None


# 全局进程注册表
_process_registry: dict[str, InteractiveProcess] = {}
_registry_lock = threading.Lock()


def start_interactive_process(
    cmd: str,
    cwd: str | None = None,
) -> InteractiveProcess:
    """启动一个交互式进程并注册

    Args:
        cmd: 命令字符串
        cwd: 工作目录

    Returns:
        InteractiveProcess 实例
    """
    logger.info(f"    [interactive] 启动: {cmd}")
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    process_id = uuid.uuid4().hex[:8]
    ip = InteractiveProcess(proc, process_id)

    with _registry_lock:
        _process_registry[process_id] = ip

    return ip


def get_interactive_process(process_id: str) -> InteractiveProcess | None:
    """根据 ID 获取交互式进程"""
    with _registry_lock:
        return _process_registry.get(process_id)


def remove_interactive_process(process_id: str) -> None:
    """从注册表中移除进程"""
    with _registry_lock:
        ip = _process_registry.pop(process_id, None)
    if ip and ip.is_running:
        ip.kill()
