"""对话日志服务 — 保存 Agent 与 LLM 的完整对话记录"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConversationEntry:
    """单条对话消息"""

    def __init__(
        self,
        role: str,
        content: Any,
        timestamp: str | None = None,
    ) -> None:
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }


class ConversationLog:
    """单次 Agent 阶段的完整对话记录"""

    def __init__(
        self,
        task_id: str,
        agent_name: str,
    ) -> None:
        self.task_id = task_id
        self.agent_name = agent_name
        self.started_at: str = datetime.now().isoformat()
        self.finished_at: str | None = None
        self.system_prompt: str = ""
        self.entries: list[ConversationEntry] = []
        self.token_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
        }
        self.tool_calls_count: int = 0
        self.iterations: int = 0

    def add_system(self, system_prompt: str) -> None:
        """记录系统提示词"""
        self.system_prompt = system_prompt

    def add_user(self, content: Any) -> None:
        """记录用户消息"""
        self.entries.append(ConversationEntry(role="user", content=content))

    def add_assistant(self, content: Any, tool_calls: list[dict[str, Any]] | None = None) -> None:
        """记录 assistant 消息"""
        entry_content: dict[str, Any] = {"text": content}
        if tool_calls:
            entry_content["tool_calls"] = tool_calls
            self.tool_calls_count += len(tool_calls)
        self.entries.append(ConversationEntry(role="assistant", content=entry_content))

    def add_tool_result(self, tool_use_id: str, tool_name: str, result: str) -> None:
        """记录工具调用结果"""
        self.entries.append(ConversationEntry(
            role="tool_result",
            content={
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "result": result[:5000],  # 避免日志过大
            },
        ))

    def add_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        """累加 token 使用"""
        self.token_usage["input_tokens"] += input_tokens
        self.token_usage["output_tokens"] += output_tokens
        self.iterations += 1

    def finish(self) -> None:
        """标记对话结束"""
        self.finished_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "system_prompt": self.system_prompt,
            "entries": [e.to_dict() for e in self.entries],
            "token_usage": self.token_usage,
            "tool_calls_count": self.tool_calls_count,
            "iterations": self.iterations,
        }


class ConversationLogger:
    """对话日志管理器

    职责:
    - 管理当前活跃的对话记录
    - 将对话持久化到磁盘
    - 按 task_id / agent_name 组织文件
    """

    def __init__(self, conversations_dir: str | Path) -> None:
        self._dir = Path(conversations_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._active_log: ConversationLog | None = None

    @property
    def active_log(self) -> ConversationLog | None:
        return self._active_log

    def start(self, task_id: str, agent_name: str) -> ConversationLog:
        """开始一个新的对话记录

        Args:
            task_id: 任务 ID
            agent_name: Agent 名称 (analyst/coder/reviewer/reflector)

        Returns:
            新建的 ConversationLog
        """
        self._active_log = ConversationLog(
            task_id=task_id,
            agent_name=agent_name,
        )
        return self._active_log

    def finish_and_save(self) -> Path | None:
        """结束当前对话并保存到文件

        Returns:
            保存的文件路径，如果没有活跃对话则返回 None
        """
        if self._active_log is None:
            return None

        log = self._active_log
        log.finish()

        # 创建 task 子目录
        safe_task_id = log.task_id.replace("/", "_").replace("\\", "_")
        task_dir = self._dir / safe_task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # 文件名: {agent_name}_{timestamp}.json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{log.agent_name}_{timestamp}.json"
        filepath = task_dir / filename

        try:
            filepath.write_text(
                json.dumps(log.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"对话日志已保存: {filepath}")
        except Exception as e:
            logger.warning(f"保存对话日志失败: {e}")
            self._active_log = None
            return None

        self._active_log = None
        return filepath

    def discard(self) -> None:
        """放弃当前对话记录，不保存"""
        self._active_log = None


def load_conversation(filepath: str | Path) -> dict[str, Any]:
    """加载单个对话日志文件

    Args:
        filepath: 对话日志文件路径

    Returns:
        对话日志字典
    """
    path = Path(filepath)
    if not path.exists():
        return {"error": f"文件不存在: {filepath}"}
    return json.loads(path.read_text(encoding="utf-8"))


def list_task_conversations(conversations_dir: str | Path, task_id: str) -> list[Path]:
    """列出某个任务的所有对话日志

    Args:
        conversations_dir: 对话日志根目录
        task_id: 任务 ID

    Returns:
        对话日志文件路径列表（按时间排序）
    """
    safe_task_id = task_id.replace("/", "_").replace("\\", "_")
    task_dir = Path(conversations_dir) / safe_task_id
    if not task_dir.exists():
        return []
    return sorted(task_dir.glob("*.json"))
