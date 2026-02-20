"""Supervisor Agent — 重试耗尽时介入决策"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task

logger = logging.getLogger(__name__)


@dataclass
class SupervisorDecision:
    """Supervisor 的决策结果"""

    action: str  # "continue" | "halt"
    reason: str
    hint: str = ""          # 给 Coder 的修复方向（action=="continue" 时有意义）
    extra_retries: int = 3  # 追加多少次重试机会（action=="continue" 时有意义）


class Supervisor(BaseAgent):
    """监督 Agent

    职责:
    - 在 Coder→Reviewer 循环耗尽重试次数后介入
    - 分析失败原因，判断是否可以继续（给更多重试 + 修复提示）
    - 或者暂停任务，等待人工介入
    """

    def execute(
        self,
        task: Task,
        context: AgentContext,
        **kwargs: Any,
    ) -> SupervisorDecision:
        """执行监督决策

        Args:
            task: 已耗尽重试的任务
            context: Agent 上下文

        Returns:
            SupervisorDecision 决策结果
        """
        system_prompt = self._load_prompt_template("supervisor.md")
        user_message = self._build_user_message(task, context)

        response = self._llm.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            conversation_log=kwargs.get("conversation_log"),
            label=f"Supervisor/{task.id}",
        )

        return self._parse_decision(response.content)

    def _build_user_message(self, task: Task, context: AgentContext) -> str:
        """构建 Supervisor 的用户消息"""
        issues_text = "无具体问题记录"
        if task.review_result and task.review_result.issues:
            issues_text = "\n".join(f"- {i}" for i in task.review_result.issues)

        suggestions_text = "无"
        if task.review_result and task.review_result.suggestions:
            suggestions_text = "\n".join(
                f"- {s}" for s in task.review_result.suggestions
            )

        completed_text = "无"
        if context.completed_tasks:
            completed_text = "\n".join(
                f"- [{tid}] {t.title}" for tid, t in context.completed_tasks.items()
            )

        return (
            f"## 任务信息\n\n"
            f"**ID**: {task.id}\n"
            f"**标题**: {task.title}\n"
            f"**描述**: {task.description}\n"
            f"**类别**: {task.category}\n\n"
            f"## 执行情况\n\n"
            f"- 已重试 **{task.retry_count}** 次（上限 {task.max_retries}），仍未通过审查\n"
            f"- 之前的 Supervisor 提示: {task.supervisor_hint or '无'}\n\n"
            f"## 最近一次审查问题\n\n"
            f"{issues_text}\n\n"
            f"## 审查建议\n\n"
            f"{suggestions_text}\n\n"
            f"## 已完成的依赖任务\n\n"
            f"{completed_text}\n\n"
            f"## 请输出决策\n\n"
            f"输出 JSON 格式：\n"
            f'{{"action": "continue"|"halt", "reason": "...", "hint": "...", "extra_retries": 3}}'
        )

    def _parse_decision(self, content: str) -> SupervisorDecision:
        """从 LLM 输出解析决策"""
        try:
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                data = json.loads(m.group())
                return SupervisorDecision(
                    action=data.get("action", "halt"),
                    reason=data.get("reason", ""),
                    hint=data.get("hint", ""),
                    extra_retries=max(1, int(data.get("extra_retries", 3))),
                )
        except Exception as e:
            logger.warning(f"Supervisor 决策解析失败: {e}，默认暂停")

        return SupervisorDecision(
            action="halt",
            reason="无法解析 Supervisor 输出，默认暂停等待人工介入",
        )
