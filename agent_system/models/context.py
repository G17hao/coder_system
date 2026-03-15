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
    budget_limit: int = 0  # Token 预算上限，0 表示不限制
    call_limit: int = 0  # API 调用次数上限，0 表示不限制
    llm_timeout: float = 300.0
    llm_max_retries: int = 4
    model_context_window: int = 200000  # 模型上下文窗口大小（token），用于计算压缩阈值
    context_compress_threshold: float = 0.7  # Token 使用率达到 70% 时触发压缩
    enable_llm_cache: bool = True  # 启用 LLM 显式缓存（DashScope/阿里百炼）
    cache_min_tokens: int = 1024  # 启用缓存的最小 token 数
    summary_trigger_bytes: int = 4_200_000  # 摘要触发的请求体阈值（字节），超过后优先生成滚动摘要
    summary_trigger_message_count: int = 24  # 首次触发摘要所需的最少消息数，避免短对话过早压缩
    summary_keep_recent_messages: int = 8  # 摘要后保留的最近消息数，确保工具循环仍有足够近因上下文
    summary_keep_recent_log_entries: int = 8  # 对话日志中保留的最近原始记录数，避免日志无限增长
    summary_min_new_messages_after_summary: int = 12  # 已有摘要后再次触发摘要前，至少新增多少条消息

@dataclass
class AgentContext:
    """Agent 运行上下文，贯穿整个执行过程"""
    project: ProjectConfig
    task_queue: list[Task] = field(default_factory=list)
    completed_tasks: dict[str, Task] = field(default_factory=dict)
    current_task: Task | None = None
    config: AgentConfig = field(default_factory=AgentConfig)
    total_tokens_used: int = 0
    total_api_calls: int = 0
