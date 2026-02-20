"""日志格式器：按 executor 标签着色输出"""

from __future__ import annotations

import logging
import re


class ExecutorColorFormatter(logging.Formatter):
    """为不同 executor 输出不同日志颜色。"""

    _RESET = "\x1b[0m"
    _LEVEL_COLORS: dict[int, str] = {
        logging.DEBUG: "\x1b[90m",      # 灰
        logging.INFO: "\x1b[37m",       # 白
        logging.WARNING: "\x1b[33m",    # 黄
        logging.ERROR: "\x1b[31m",      # 红
        logging.CRITICAL: "\x1b[91m",   # 亮红
    }
    _EXECUTOR_COLORS: dict[str, str] = {
        "Analyst": "\x1b[36m",      # 青
        "Coder": "\x1b[32m",        # 绿
        "Reviewer": "\x1b[35m",     # 紫
        "Supervisor": "\x1b[33m",   # 黄
        "Reflector": "\x1b[34m",    # 蓝
        "CommitMsg": "\x1b[96m",    # 亮青
        "LLM": "\x1b[37m",          # 白
    }
    _EXECUTOR_PATTERN = re.compile(r"\[(Analyst|Coder|Reviewer|Supervisor|Reflector|CommitMsg|LLM)(?:/[^\]]*)?\]")

    def __init__(self, fmt: str, use_color: bool) -> None:
        super().__init__(fmt=fmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self._use_color:
            return text

        executor = self._extract_executor(text)
        if executor:
            color = self._EXECUTOR_COLORS.get(executor)
        else:
            color = self._LEVEL_COLORS.get(record.levelno)

        if not color:
            return text
        return f"{color}{text}{self._RESET}"

    def _extract_executor(self, text: str) -> str | None:
        match = self._EXECUTOR_PATTERN.search(text)
        if not match:
            return None
        return match.group(1)
