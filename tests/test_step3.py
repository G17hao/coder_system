"""Step 3 测试：Agent 基类 + Planner"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_system.agents.base import BaseAgent
from agent_system.agents.planner import (
    CyclicDependencyError,
    DependencyStatus,
    Planner,
)
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import Task, TaskStatus


def _make_context(tasks: list[Task] | None = None) -> AgentContext:
    """创建测试用 AgentContext"""
    config = ProjectConfig(
        project_name="test",
        project_description="test project",
        project_root="/tmp",
        task_categories=["infrastructure", "model"],
    )
    ctx = AgentContext(
        project=config,
        task_queue=tasks or [],
        config=AgentConfig(max_dynamic_tasks=10),
    )
    return ctx


def _make_task(
    tid: str,
    deps: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    """快速创建测试任务"""
    return Task(
        id=tid,
        title=f"Task {tid}",
        description=f"Description for {tid}",
        status=status,
        dependencies=deps or [],
    )


class TestDependencyCheck:
    """Planner 依赖检查测试"""

    def test_ready_when_all_deps_done(self) -> None:
        """依赖全部 done → 任务放行（返回 READY）"""
        planner = Planner(llm=MagicMock())
        task_a = _make_task("T1", deps=["T0"])
        completed = {"T0": _make_task("T0", status=TaskStatus.DONE)}
        result = planner.check_dependencies(task_a, completed)
        assert result == DependencyStatus.READY

    def test_ready_when_no_deps(self) -> None:
        """无依赖 → READY"""
        planner = Planner(llm=MagicMock())
        task = _make_task("T0")
        result = planner.check_dependencies(task, {})
        assert result == DependencyStatus.READY

    def test_blocked_when_dep_not_done(self) -> None:
        """依赖在队列中但未完成 → BLOCKED"""
        planner = Planner(llm=MagicMock())
        task_a = _make_task("T1", deps=["T0"])
        ctx = _make_context([_make_task("T0"), task_a])
        result = planner.check_dependencies(task_a, {}, context=ctx)
        assert result == DependencyStatus.BLOCKED

    def test_missing_when_dep_unknown(self) -> None:
        """依赖不存在于已知 ID 集合 → MISSING"""
        planner = Planner(llm=MagicMock())
        task_a = _make_task("T1", deps=["T0"])
        result = planner.check_dependencies(task_a, {}, known_ids=set())
        assert result == DependencyStatus.MISSING


class TestCycleDetection:
    """循环依赖检测测试"""

    def test_no_cycle(self) -> None:
        """无循环 → 不抛异常"""
        planner = Planner(llm=MagicMock())
        tasks = [
            _make_task("A"),
            _make_task("B", deps=["A"]),
            _make_task("C", deps=["B"]),
        ]
        planner.validate_no_cycles(tasks)  # 不应抛异常

    def test_direct_cycle(self) -> None:
        """直接循环 A↔B → 抛 CyclicDependencyError"""
        planner = Planner(llm=MagicMock())
        task_x = _make_task("X", deps=["Y"])
        task_y = _make_task("Y", deps=["X"])
        with pytest.raises(CyclicDependencyError):
            planner.validate_no_cycles([task_x, task_y])

    def test_indirect_cycle(self) -> None:
        """间接循环 A→B→C→A → 抛 CyclicDependencyError"""
        planner = Planner(llm=MagicMock())
        tasks = [
            _make_task("A", deps=["C"]),
            _make_task("B", deps=["A"]),
            _make_task("C", deps=["B"]),
        ]
        with pytest.raises(CyclicDependencyError):
            planner.validate_no_cycles(tasks)


class TestGenerateMissing:
    """动态任务生成测试"""

    def test_generate_tasks_from_llm(self) -> None:
        """LLM 返回有效 JSON → 解析出新任务"""
        mock_llm = MagicMock()
        mock_llm.call.return_value = MagicMock(
            content='[{"id": "T0", "title": "新任务", "description": "自动生成"}]'
        )
        planner = Planner(llm=mock_llm)
        ctx = _make_context()
        new_tasks = planner.generate_missing(["T0"], ctx)
        assert len(new_tasks) == 1
        assert new_tasks[0].id == "T0"
        assert new_tasks[0].created_by == "planner"

    def test_generate_empty_when_no_missing(self) -> None:
        """无缺失 ID → 返回空列表"""
        planner = Planner(llm=MagicMock())
        ctx = _make_context()
        assert planner.generate_missing([], ctx) == []

    def test_generate_respects_limit(self) -> None:
        """超出 max_dynamic_tasks → 不再生成"""
        planner = Planner(llm=MagicMock())
        ctx = _make_context()
        ctx.config.max_dynamic_tasks = 0  # 上限为 0
        result = planner.generate_missing(["T0"], ctx)
        assert result == []


class TestGetNextPending:
    """获取下一个可执行任务测试"""

    def test_returns_highest_priority(self) -> None:
        """返回优先级最高（数字最小）的 READY 任务"""
        planner = Planner(llm=MagicMock())
        t1 = _make_task("T1")
        t1.priority = 10
        t2 = _make_task("T2")
        t2.priority = 5
        ctx = _make_context([t1, t2])
        result = planner.get_next_pending(ctx)
        assert result is not None
        assert result.id == "T2"

    def test_returns_none_when_all_blocked(self) -> None:
        """所有 pending 任务都被阻塞 → 返回 None"""
        planner = Planner(llm=MagicMock())
        t1 = _make_task("T1", deps=["T0"])  # T0 不存在于已完成中
        ctx = _make_context([t1])
        result = planner.get_next_pending(ctx)
        assert result is None

    def test_returns_none_when_empty(self) -> None:
        """空队列 → 返回 None"""
        planner = Planner(llm=MagicMock())
        ctx = _make_context([])
        result = planner.get_next_pending(ctx)
        assert result is None


class TestBaseAgent:
    """BaseAgent 基类测试"""

    def test_template_rendering(self) -> None:
        """模板变量替换正确"""
        planner = Planner(llm=MagicMock())
        result = planner._render_template(
            "Hello {{name}}, welcome to {{project}}!",
            {"name": "Agent", "project": "TestProject"},
        )
        assert result == "Hello Agent, welcome to TestProject!"

    def test_agent_name(self) -> None:
        """Agent 名称为类名"""
        planner = Planner(llm=MagicMock())
        assert planner.name == "Planner"
