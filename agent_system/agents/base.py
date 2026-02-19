"""Agent 抽象基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_system.models.context import AgentContext
from agent_system.models.task import Task


class BaseAgent(ABC):
    """所有 Agent 的抽象基类

    子类须实现 execute() 方法。
    """

    def __init__(self, llm: Any) -> None:
        """
        Args:
            llm: LLMService 实例（或 mock）
        """
        self._llm = llm

    @property
    def name(self) -> str:
        """Agent 名称"""
        return self.__class__.__name__

    @abstractmethod
    def execute(self, task: Task, context: AgentContext, **kwargs: Any) -> Any:
        """执行 Agent 任务

        Args:
            task: 当前任务
            context: Agent 上下文
            **kwargs: 额外参数

        Returns:
            Agent 执行结果（子类定义具体类型）
        """
        ...

    def _load_prompt_template(self, template_name: str) -> str:
        """加载 prompt 模板文件

        Args:
            template_name: 模板文件名（不含路径前缀），如 "planner.md"

        Returns:
            模板内容字符串
        """
        from pathlib import Path
        prompt_dir = Path(__file__).parent.parent / "prompts"
        template_path = prompt_dir / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Prompt 模板不存在: {template_path}")
        return template_path.read_text(encoding="utf-8")

    def _render_template(self, template: str, variables: dict[str, str]) -> str:
        """渲染模板变量（{{variableName}} 语法）

        Args:
            template: 模板字符串
            variables: 变量字典

        Returns:
            渲染后的字符串
        """
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{{{key}}}}}", value)
        return result
