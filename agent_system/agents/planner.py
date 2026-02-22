"""Planner Agent — 任务规划与依赖管理"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task, TaskStatus


class DependencyStatus(str, Enum):
    """依赖检查结果"""
    READY = "ready"          # 所有依赖已完成
    BLOCKED = "blocked"      # 依赖在队列中但未完成
    MISSING = "missing"      # 依赖不存在于队列中


class CyclicDependencyError(Exception):
    """循环依赖错误"""
    pass


class Planner(BaseAgent):
    """任务规划 Agent

    职责:
    - 检查任务前置依赖是否满足
    - 当发现缺失依赖时动态生成新任务
    - 循环依赖检测
    - 任务优先级管理
    """

    def execute(self, task: Task, context: AgentContext, **kwargs: Any) -> DependencyStatus:
        """检查任务依赖并返回状态

        Args:
            task: 当前任务
            context: Agent 上下文

        Returns:
            DependencyStatus 枚举值
        """
        return self.check_dependencies(task, context.completed_tasks, context=context)

    def check_dependencies(
        self,
        task: Task,
        completed: dict[str, Task],
        known_ids: set[str] | None = None,
        context: AgentContext | None = None,
    ) -> DependencyStatus:
        """检查任务的前置依赖状态

        Args:
            task: 要检查的任务
            completed: 已完成任务字典 {id: Task}
            known_ids: 已知的所有任务 ID 集合（用于检测缺失依赖）
            context: Agent 上下文（可选，用于获取队列中所有任务）

        Returns:
            DependencyStatus
        """
        if not task.dependencies:
            return DependencyStatus.READY

        # 构建已知 ID 集合
        if known_ids is None:
            known_ids = set(completed.keys())
            if context is not None:
                known_ids.update(t.id for t in context.task_queue)

        for dep_id in task.dependencies:
            if dep_id in completed and completed[dep_id].status == TaskStatus.DONE:
                continue
            if dep_id not in known_ids:
                return DependencyStatus.MISSING
            return DependencyStatus.BLOCKED

        return DependencyStatus.READY

    def generate_missing(
        self,
        missing_ids: list[str],
        context: AgentContext,
    ) -> list[Task]:
        """使用 LLM 动态生成缺失的依赖任务

        Args:
            missing_ids: 缺失的任务 ID 列表
            context: Agent 上下文

        Returns:
            生成的新任务列表
        """
        if not missing_ids:
            return []

        # 检查动态任务生成上限
        existing_dynamic = sum(
            1 for t in context.task_queue if t.created_by == "planner"
        )
        remaining = context.config.max_dynamic_tasks - existing_dynamic
        if remaining <= 0:
            return []

        system_prompt = self._build_system_prompt(context)
        user_message = (
            f"以下任务 ID 在队列中缺失，请为它们生成任务定义：\n"
            f"缺失 ID: {json.dumps(missing_ids)}\n\n"
            f"已有任务 ID: {json.dumps([t.id for t in context.task_queue])}\n\n"
            f"请输出 JSON 数组格式的任务定义，每个任务包含: "
            f"id, title, description, dependencies, priority, phase, category"
        )

        response = self._llm.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        return self._parse_generated_tasks(response.content, limit=remaining)

    def validate_no_cycles(self, tasks: list[Task]) -> None:
        """检测任务依赖是否存在循环

        Args:
            tasks: 任务列表

        Raises:
            CyclicDependencyError: 发现循环依赖时抛出
        """
        # 构建邻接表
        graph: dict[str, list[str]] = {}
        for t in tasks:
            graph[t.id] = list(t.dependencies)

        # DFS 检测环
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in graph}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in graph.get(node, []):
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    return True  # 找到环
                if color[neighbor] == WHITE and dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for node in list(graph.keys()):
            if color[node] == WHITE:
                if dfs(node):
                    raise CyclicDependencyError(
                        f"检测到循环依赖，涉及任务: "
                        f"{[tid for tid, c in color.items() if c == GRAY]}"
                    )

    def get_next_pending(self, context: AgentContext) -> Task | None:
        """获取优先级最高的可执行 pending 任务

        Args:
            context: Agent 上下文

        Returns:
            下一个可执行任务，无则返回 None
        """
        pending = [
            t for t in context.task_queue
            if t.status == TaskStatus.PENDING
        ]
        if not pending:
            return None

        # 按 priority 排序（越小越优先）
        pending.sort(key=lambda t: (t.priority, t.phase))

        for task in pending:
            status = self.check_dependencies(task, context.completed_tasks, context=context)
            if status == DependencyStatus.READY:
                return task

        return None

    def _build_system_prompt(self, context: AgentContext) -> str:
        """构建 Planner 的系统提示词"""
        template = self._load_prompt_template("planner.md")
        prompt_overrides = getattr(context.project, "prompt_overrides", {}) or {}
        project_specific_prompt = str(prompt_overrides.get("planner", "")).strip()
        return self._render_template(template, {
            "projectDescription": context.project.project_description,
            "taskCategories": json.dumps(context.project.task_categories, ensure_ascii=False),
            "projectSpecificPrompt": project_specific_prompt or "无",
        })

    def _parse_generated_tasks(self, content: str, limit: int) -> list[Task]:
        """解析 LLM 生成的任务 JSON

        Args:
            content: LLM 输出内容
            limit: 最大任务数量

        Returns:
            Task 列表
        """
        try:
            # 尝试从 content 中提取 JSON 数组
            start = content.find("[")
            end = content.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            json_str = content[start:end]
            items = json.loads(json_str)

            tasks: list[Task] = []
            for item in items[:limit]:
                tasks.append(Task(
                    id=item["id"],
                    title=item["title"],
                    description=item.get("description", ""),
                    dependencies=item.get("dependencies", []),
                    priority=item.get("priority", 0),
                    phase=item.get("phase", 0),
                    category=item.get("category", ""),
                    created_by="planner",
                ))
            return tasks
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
