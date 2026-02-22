"""项目配置模型定义 + 加载/校验"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PatternMapping:
    """跨语言/框架模式映射"""
    from_pattern: str
    to_pattern: str

    def to_dict(self) -> dict:
        return {"from_pattern": self.from_pattern, "to_pattern": self.to_pattern}

    @classmethod
    def from_dict(cls, data: dict) -> PatternMapping:
        return cls(
            from_pattern=data["from_pattern"],
            to_pattern=data["to_pattern"],
        )


@dataclass
class TaskSeed:
    """初始任务种子"""
    id: str
    title: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    phase: int = 0
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "dependencies": self.dependencies,
            "priority": self.priority,
            "phase": self.phase,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskSeed:
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            dependencies=data.get("dependencies", []),
            priority=data.get("priority", 0),
            phase=data.get("phase", 0),
            category=data.get("category", ""),
        )


@dataclass
class EmailApprovalConfig:
    """Supervisor 暂停后的邮件通知与审批配置"""

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password_env: str = "AGENT_SMTP_PASSWORD"
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password_env: str = "AGENT_IMAP_PASSWORD"
    notify_to: str = ""
    notify_from: str = ""
    approval_sender: str = ""
    subject_prefix: str = "[AgentSystem]"
    poll_interval_sec: int = 15
    max_wait_sec: int = 1800

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_user": self.smtp_user,
            "smtp_password_env": self.smtp_password_env,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_user": self.imap_user,
            "imap_password_env": self.imap_password_env,
            "notify_to": self.notify_to,
            "notify_from": self.notify_from,
            "approval_sender": self.approval_sender,
            "subject_prefix": self.subject_prefix,
            "poll_interval_sec": self.poll_interval_sec,
            "max_wait_sec": self.max_wait_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EmailApprovalConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            smtp_host=str(data.get("smtp_host", "")),
            smtp_port=int(data.get("smtp_port", 465)),
            smtp_user=str(data.get("smtp_user", "")),
            smtp_password_env=str(data.get("smtp_password_env", "AGENT_SMTP_PASSWORD")),
            imap_host=str(data.get("imap_host", "")),
            imap_port=int(data.get("imap_port", 993)),
            imap_user=str(data.get("imap_user", "")),
            imap_password_env=str(data.get("imap_password_env", "AGENT_IMAP_PASSWORD")),
            notify_to=str(data.get("notify_to", "")),
            notify_from=str(data.get("notify_from", "")),
            approval_sender=str(data.get("approval_sender", "")),
            subject_prefix=str(data.get("subject_prefix", "[AgentSystem]")),
            poll_interval_sec=max(1, int(data.get("poll_interval_sec", 15))),
            max_wait_sec=max(60, int(data.get("max_wait_sec", 1800))),
        )


# 必填字段列表
_REQUIRED_FIELDS = [
    "project_name",
    "project_description",
    "project_root",
    "reference_roots",
    "git_branch",
    "coding_conventions",
    "review_checklist",
    "review_commands",
    "task_categories",
    "initial_tasks",
]


@dataclass
class ProjectConfig:
    """外部注入的项目配置"""
    project_name: str
    project_description: str
    project_root: str
    reference_roots: list[str] = field(default_factory=list)
    git_branch: str = "feat/agent-auto"
    coding_conventions: str = ""
    pattern_mappings: list[PatternMapping] = field(default_factory=list)
    review_checklist: list[str] = field(default_factory=list)
    review_commands: list[str] = field(default_factory=list)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    email_approval: EmailApprovalConfig = field(default_factory=EmailApprovalConfig)
    task_categories: list[str] = field(default_factory=list)
    initial_tasks: list[TaskSeed] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "project_description": self.project_description,
            "project_root": self.project_root,
            "reference_roots": self.reference_roots,
            "git_branch": self.git_branch,
            "coding_conventions": self.coding_conventions,
            "pattern_mappings": [m.to_dict() for m in self.pattern_mappings],
            "review_checklist": self.review_checklist,
            "review_commands": self.review_commands,
            "prompt_overrides": self.prompt_overrides,
            "email_approval": self.email_approval.to_dict(),
            "task_categories": self.task_categories,
            "initial_tasks": [t.to_dict() for t in self.initial_tasks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectConfig:
        """从字典创建，校验必填字段"""
        missing = [f for f in _REQUIRED_FIELDS if f not in data]
        if missing:
            raise ValueError(f"项目配置缺少必填字段: {', '.join(missing)}")

        return cls(
            project_name=data["project_name"],
            project_description=data["project_description"],
            project_root=data["project_root"],
            reference_roots=data.get("reference_roots", []),
            git_branch=data.get("git_branch", "feat/agent-auto"),
            coding_conventions=data.get("coding_conventions", ""),
            pattern_mappings=[
                PatternMapping.from_dict(m)
                for m in data.get("pattern_mappings", [])
            ],
            review_checklist=data.get("review_checklist", []),
            review_commands=data.get("review_commands", []),
            prompt_overrides=data.get("prompt_overrides", {}),
            email_approval=EmailApprovalConfig.from_dict(data.get("email_approval", {})),
            task_categories=data.get("task_categories", []),
            initial_tasks=[
                TaskSeed.from_dict(t) for t in data.get("initial_tasks", [])
            ],
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ProjectConfig:
        """从 JSON 文件加载项目配置"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"项目配置文件不存在: {p}")
        if not p.suffix == ".json":
            raise ValueError(f"项目配置文件必须是 .json 格式: {p}")

        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls.from_dict(data)
