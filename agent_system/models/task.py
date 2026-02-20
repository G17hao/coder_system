"""任务模型定义"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Literal


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ReviewResult:
    """审查结果"""
    passed: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    context_for_coder: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ReviewResult:
        return cls(
            passed=data["passed"],
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            context_for_coder=data.get("context_for_coder", ""),
        )


@dataclass
class Task:
    """原子任务"""
    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    phase: int = 0
    category: str = ""
    created_by: Literal["initial", "planner"] = "initial"
    analysis_cache: str | None = None
    coder_output: str | None = None
    review_result: ReviewResult | None = None
    retry_count: int = 0
    max_retries: int = 20
    error: str | None = None
    commit_hash: str | None = None
    supervisor_hint: str | None = None
    supervisor_plan: str | None = None
    supervisor_must_change_files: list[str] = field(default_factory=list)
    analysis_subtasks_generated: bool = False
    modified_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d["status"] = self.status.value
        if self.review_result is not None:
            d["review_result"] = self.review_result.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        """从字典反序列化"""
        review_data = data.get("review_result")
        review_result = ReviewResult.from_dict(review_data) if review_data else None

        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            status=TaskStatus(data.get("status", "pending")),
            dependencies=data.get("dependencies", []),
            priority=data.get("priority", 0),
            phase=data.get("phase", 0),
            category=data.get("category", ""),
            created_by=data.get("created_by", "initial"),
            analysis_cache=data.get("analysis_cache"),
            coder_output=data.get("coder_output"),
            review_result=review_result,
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 20),
            error=data.get("error"),
            commit_hash=data.get("commit_hash"),
            supervisor_hint=data.get("supervisor_hint"),
            supervisor_plan=data.get("supervisor_plan"),
            supervisor_must_change_files=data.get("supervisor_must_change_files", []),
            analysis_subtasks_generated=data.get("analysis_subtasks_generated", False),
            modified_files=data.get("modified_files", []),
        )

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> Task:
        """从 JSON 字符串反序列化"""
        return cls.from_dict(json.loads(json_str))
