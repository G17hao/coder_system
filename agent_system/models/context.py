"""Agent 上下文与配置"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import Task


@dataclass
class AgentConfig:
    """Agent 系统运行配置"""
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.0
    project_config_file: str = ""
    state_file: str = "state/tasks.json"
    git_auto_commit: bool = True
    dry_run: bool = False
    max_dynamic_tasks: int = 10
    budget_limit: int = 500_000
    llm_timeout: float = 180.0
    llm_max_retries: int = 2

@dataclass
class AgentContext:
    """Agent 运行上下文，贯穿整个执行过程"""
    project: ProjectConfig
    task_queue: list[Task] = field(default_factory=list)
    completed_tasks: dict[str, Task] = field(default_factory=dict)
    current_task: Task | None = None
    config: AgentConfig = field(default_factory=AgentConfig)
    total_tokens_used: int = 0
