"""Reflector Agent — 任务执行后反思"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class ReflectionReport:
    """反思报告数据结构"""

    def __init__(
        self,
        task_id: str,
        task_title: str,
        raw: dict[str, Any],
    ) -> None:
        self.task_id = task_id
        self.task_title = task_title
        self.raw = raw

    @property
    def execution_summary(self) -> dict[str, Any]:
        return self.raw.get("execution_summary", {})

    @property
    def lessons_learned(self) -> list[str]:
        return self.raw.get("lessons_learned", [])

    @property
    def improvement_suggestions(self) -> dict[str, list[str]]:
        return self.raw.get("improvement_suggestions", {})

    @property
    def best_practices(self) -> list[str]:
        return self.raw.get("best_practices", [])

    @property
    def risk_warnings(self) -> list[str]:
        return self.raw.get("risk_warnings", [])

    def to_dict(self) -> dict[str, Any]:
        return self.raw

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReflectionReport:
        return cls(
            task_id=data.get("task_id", ""),
            task_title=data.get("task_title", ""),
            raw=data,
        )


class Reflector(BaseAgent):
    """反思 Agent

    职责:
    - 每次任务执行后进行系统性反思
    - 分析执行质量、模式与经验教训
    - 提出对 Agent 系统的改进建议
    - 将反思报告持久化到反思目录
    """

    def execute(
        self,
        task: Task,
        context: AgentContext,
        **kwargs: Any,
    ) -> ReflectionReport:
        """执行反思

        Args:
            task: 刚完成/失败的任务
            context: Agent 上下文

        Returns:
            ReflectionReport 反思报告
        """
        system_prompt = self.build_system_prompt(context)
        user_message = self._build_user_message(task, context)

        response = self._llm.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            conversation_log=kwargs.get("conversation_log"),
        )

        report = self._parse_report(task, response.content)

        return report

    def build_system_prompt(self, context: AgentContext) -> str:
        """构建反思 Agent 的系统提示词

        Args:
            context: Agent 上下文

        Returns:
            渲染后的系统提示词
        """
        template = self._load_prompt_template("reflector.md")

        conventions = getattr(context.project, "coding_conventions", "")

        completed_text = ""
        if context.completed_tasks:
            for tid, t in context.completed_tasks.items():
                status = "pass" if t.status == TaskStatus.DONE else "fail"
                completed_text += f"- [{status}] {tid}: {t.title} (retries={t.retry_count})\n"

        return self._render_template(template, {
            "codingConventions": conventions or "无",
            "completedTasks": completed_text or "无",
        })

    def _build_user_message(self, task: Task, context: AgentContext) -> str:
        """构建用户消息，包含任务执行的完整上下文

        Args:
            task: 刚完成/失败的任务
            context: Agent 上下文

        Returns:
            用户消息
        """
        status_label = "成功" if task.status == TaskStatus.DONE else "失败"

        # 收集执行过程摘要
        analysis_snippet = (task.analysis_cache or "无")[:1500]
        coder_snippet = (task.coder_output or "无")[:1500]

        review_info = "无"
        if task.review_result:
            review_info = json.dumps(
                task.review_result.to_dict(),
                ensure_ascii=False,
                indent=2,
            )

        error_info = task.error or "无"

        message = (
            f"## 请对以下任务执行过程进行反思\n\n"
            f"**任务 ID**: {task.id}\n"
            f"**标题**: {task.title}\n"
            f"**描述**: {task.description}\n"
            f"**执行结果**: {status_label}\n"
            f"**重试次数**: {task.retry_count}\n\n"
            f"### 分析阶段输出（摘要）\n"
            f"```\n{analysis_snippet}\n```\n\n"
            f"### 编码阶段输出（摘要）\n"
            f"```\n{coder_snippet}\n```\n\n"
            f"### 审查结果\n"
            f"```json\n{review_info}\n```\n\n"
            f"### 错误信息\n"
            f"{error_info}\n\n"
            f"### 上下文\n"
            f"- 已完成任务数: {len(context.completed_tasks)}\n"
            f"- 总 Token 消耗: {context.total_tokens_used}\n\n"
            f"请输出 JSON 格式的反思报告。"
        )
        return message

    def _parse_report(self, task: Task, content: str) -> ReflectionReport:
        """解析 LLM 输出的反思报告

        Args:
            task: 任务
            content: LLM 输出

        Returns:
            ReflectionReport
        """
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                return self._fallback_report(task, "无法从 LLM 输出中提取 JSON")
            data = json.loads(content[start:end])
            data.setdefault("task_id", task.id)
            data.setdefault("task_title", task.title)
            return ReflectionReport.from_dict(data)
        except json.JSONDecodeError as e:
            return self._fallback_report(task, f"JSON 解析失败: {e}")

    def _fallback_report(self, task: Task, reason: str) -> ReflectionReport:
        """解析失败时的兜底报告

        Args:
            task: 任务
            reason: 失败原因

        Returns:
            基本的 ReflectionReport
        """
        return ReflectionReport(
            task_id=task.id,
            task_title=task.title,
            raw={
                "task_id": task.id,
                "task_title": task.title,
                "execution_summary": {
                    "analysis_quality": "unknown",
                    "coding_quality": "unknown",
                    "review_quality": "unknown",
                    "retry_count": task.retry_count,
                    "passed_review": task.status == TaskStatus.DONE,
                },
                "lessons_learned": [reason],
                "improvement_suggestions": {},
                "best_practices": [],
                "risk_warnings": [],
            },
        )


def save_reflection(
    report: ReflectionReport,
    reflections_dir: str | Path,
) -> Path:
    """将反思报告持久化到文件

    文件命名: {timestamp}_{task_id}.json

    Args:
        report: 反思报告
        reflections_dir: 反思目录路径

    Returns:
        写入的文件路径
    """
    dir_path = Path(reflections_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = report.task_id.replace("/", "_").replace("\\", "_")
    filename = f"{timestamp}_{safe_id}.json"
    filepath = dir_path / filename

    data = report.to_dict()
    data["_meta"] = {
        "timestamp": datetime.now().isoformat(),
        "task_id": report.task_id,
        "task_title": report.task_title,
    }

    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(f"反思报告已保存: {filepath}")
    return filepath


def load_recent_reflections(
    reflections_dir: str | Path,
    limit: int = 10,
) -> list[ReflectionReport]:
    """加载最近的反思报告

    Args:
        reflections_dir: 反思目录路径
        limit: 最多返回条数

    Returns:
        按时间倒序排列的反思报告列表
    """
    dir_path = Path(reflections_dir)
    if not dir_path.exists():
        return []

    files = sorted(dir_path.glob("*.json"), reverse=True)[:limit]
    reports: list[ReflectionReport] = []

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append(ReflectionReport.from_dict(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"加载反思报告 {f} 失败: {e}")

    return reports
