"""Step 9: Reflector Agent 测试"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_system.agents.reflector import (
    Reflector,
    ReflectionReport,
    save_reflection,
    load_recent_reflections,
)
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import Task, TaskStatus, ReviewResult


def _make_context() -> AgentContext:
    """创建测试用 AgentContext"""
    project = MagicMock(spec=ProjectConfig)
    project.coding_conventions = "TypeScript strict mode"
    project.review_checklist = ["check imports"]
    project.review_commands = []
    project.project_root = "/test"
    return AgentContext(project=project, config=AgentConfig())


def _make_task(status: TaskStatus = TaskStatus.DONE) -> Task:
    """创建测试用 Task"""
    return Task(
        id="T-1",
        title="Test Task",
        description="A test task for reflection",
        status=status,
        retry_count=0 if status == TaskStatus.DONE else 2,
        analysis_cache="found 3 relevant files",
        coder_output='{"files": []}',
        review_result=ReviewResult(
            passed=status == TaskStatus.DONE,
            issues=[] if status == TaskStatus.DONE else ["type error"],
        ),
    )


# ── ReflectionReport ────────────────────────────────────────────

class TestReflectionReport:
    """ReflectionReport 数据结构测试"""

    def test_from_dict(self) -> None:
        data = {
            "task_id": "T-1",
            "task_title": "Test",
            "execution_summary": {"analysis_quality": "good"},
            "lessons_learned": ["lesson 1"],
            "improvement_suggestions": {"tools": ["add X"]},
            "best_practices": ["practice 1"],
            "risk_warnings": ["warning 1"],
        }
        report = ReflectionReport.from_dict(data)
        assert report.task_id == "T-1"
        assert report.execution_summary["analysis_quality"] == "good"
        assert report.lessons_learned == ["lesson 1"]
        assert report.best_practices == ["practice 1"]
        assert report.risk_warnings == ["warning 1"]

    def test_to_dict_roundtrip(self) -> None:
        data = {"task_id": "T-2", "task_title": "Test 2", "lessons_learned": ["a"]}
        report = ReflectionReport.from_dict(data)
        assert report.to_dict() == data

    def test_missing_fields_default(self) -> None:
        report = ReflectionReport.from_dict({"task_id": "T-3", "task_title": "T3"})
        assert report.lessons_learned == []
        assert report.improvement_suggestions == {}
        assert report.best_practices == []


# ── Reflector Agent ─────────────────────────────────────────────

class TestReflectorAgent:
    """Reflector Agent 测试"""

    def test_system_prompt_injection(self) -> None:
        """系统提示词正确注入变量"""
        llm = MagicMock()
        reflector = Reflector(llm=llm)
        context = _make_context()
        context.completed_tasks = {
            "T-0": _make_task(TaskStatus.DONE),
        }

        prompt = reflector.build_system_prompt(context)
        assert "TypeScript strict mode" in prompt
        assert "T-0" in prompt
        assert "pass" in prompt

    def test_reflector_success(self) -> None:
        """正常反思流程"""
        llm = MagicMock()
        llm.call.return_value = MagicMock(
            content=json.dumps({
                "task_id": "T-1",
                "task_title": "Test Task",
                "execution_summary": {
                    "analysis_quality": "good",
                    "coding_quality": "good",
                    "review_quality": "good",
                    "retry_count": 0,
                    "passed_review": True,
                },
                "lessons_learned": ["TypeScript imports need careful handling"],
                "improvement_suggestions": {
                    "tools": ["add AST parser"],
                },
                "best_practices": ["always check imports"],
                "risk_warnings": [],
            }),
        )

        reflector = Reflector(llm=llm)
        context = _make_context()
        task = _make_task(TaskStatus.DONE)

        report = reflector.execute(task, context)
        assert report.task_id == "T-1"
        assert report.execution_summary["analysis_quality"] == "good"
        assert len(report.lessons_learned) >= 1
        assert "tools" in report.improvement_suggestions

    def test_reflector_parse_failure(self) -> None:
        """LLM 输出无法解析时返回兜底报告"""
        llm = MagicMock()
        llm.call.return_value = MagicMock(content="This is not JSON at all")

        reflector = Reflector(llm=llm)
        context = _make_context()
        task = _make_task(TaskStatus.FAILED)

        report = reflector.execute(task, context)
        assert report.task_id == "T-1"
        assert "无法从 LLM 输出中提取 JSON" in report.lessons_learned[0]

    def test_reflector_json_decode_error(self) -> None:
        """LLM 输出 JSON 格式错误时兜底"""
        llm = MagicMock()
        llm.call.return_value = MagicMock(content="{invalid json}")

        reflector = Reflector(llm=llm)
        context = _make_context()
        task = _make_task(TaskStatus.DONE)

        report = reflector.execute(task, context)
        assert "JSON 解析失败" in report.lessons_learned[0]

    def test_user_message_contains_task_info(self) -> None:
        """用户消息包含完整任务信息"""
        llm = MagicMock()
        reflector = Reflector(llm=llm)
        context = _make_context()
        task = _make_task(TaskStatus.DONE)

        msg = reflector._build_user_message(task, context)
        assert "T-1" in msg
        assert "Test Task" in msg
        assert "成功" in msg
        assert "found 3 relevant files" in msg

    def test_user_message_failed_task(self) -> None:
        """失败任务的用户消息"""
        llm = MagicMock()
        reflector = Reflector(llm=llm)
        context = _make_context()
        task = _make_task(TaskStatus.FAILED)
        task.error = "compilation failed"

        msg = reflector._build_user_message(task, context)
        assert "失败" in msg
        assert "compilation failed" in msg


# ── save / load reflections ─────────────────────────────────────

class TestReflectionPersistence:
    """反思报告持久化测试"""

    def test_save_and_load(self, tmp_path: Path) -> None:
        """保存并加载反思报告"""
        report = ReflectionReport.from_dict({
            "task_id": "T-1",
            "task_title": "Test",
            "lessons_learned": ["lesson 1", "lesson 2"],
            "improvement_suggestions": {"tools": ["add grep"]},
        })

        filepath = save_reflection(report, tmp_path)
        assert filepath.exists()
        assert filepath.suffix == ".json"
        assert "T-1" in filepath.name

        # 验证文件内容
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["task_id"] == "T-1"
        assert "_meta" in data
        assert data["_meta"]["task_id"] == "T-1"

    def test_load_recent(self, tmp_path: Path) -> None:
        """加载最近的反思报告"""
        for i in range(5):
            report = ReflectionReport.from_dict({
                "task_id": f"T-{i}",
                "task_title": f"Task {i}",
            })
            save_reflection(report, tmp_path)

        reports = load_recent_reflections(tmp_path, limit=3)
        assert len(reports) == 3

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        """空目录返回空列表"""
        reports = load_recent_reflections(tmp_path)
        assert reports == []

    def test_load_nonexistent_dir(self) -> None:
        """目录不存在返回空列表"""
        reports = load_recent_reflections("/nonexistent/dir")
        assert reports == []

    def test_save_creates_dir(self, tmp_path: Path) -> None:
        """自动创建目录"""
        nested = tmp_path / "a" / "b" / "reflections"
        report = ReflectionReport.from_dict({
            "task_id": "T-1",
            "task_title": "Test",
        })
        filepath = save_reflection(report, nested)
        assert filepath.exists()
        assert nested.exists()

    def test_special_chars_in_task_id(self, tmp_path: Path) -> None:
        """任务 ID 含特殊字符时安全处理"""
        report = ReflectionReport.from_dict({
            "task_id": "phase1/task-01",
            "task_title": "Test",
        })
        filepath = save_reflection(report, tmp_path)
        assert filepath.exists()
        assert "/" not in filepath.name


# ── Orchestrator 集成 ───────────────────────────────────────────

class TestOrchestratorReflection:
    """反思与 Orchestrator 集成测试"""

    def test_reflection_called_on_success(self, tmp_path: Path) -> None:
        """任务成功时调用反思"""
        from agent_system.orchestrator import Orchestrator
        from agent_system.agents.coder import CodeChanges

        # Mock agents
        planner = MagicMock()
        analyst = MagicMock()
        analyst.execute.return_value = '{"analysis": "ok"}'
        coder = MagicMock()
        coder.execute.return_value = CodeChanges(files=[])
        reviewer = MagicMock()
        reviewer.execute.return_value = ReviewResult(passed=True)
        reflector = MagicMock()
        reflector.execute.return_value = ReflectionReport.from_dict({
            "task_id": "T-1",
            "task_title": "Test",
        })

        config = AgentConfig(dry_run=False)
        context = _make_context()

        orch = Orchestrator(
            config=config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            reflector=reflector,
            context=context,
        )
        orch._reflections_dir = tmp_path
        orch._state_store = MagicMock()
        orch._git = None
        orch._file_service = MagicMock()

        task = _make_task(TaskStatus.PENDING)
        task.status = TaskStatus.PENDING
        orch.run_single_task(task)

        assert task.status == TaskStatus.DONE
        reflector.execute.assert_called_once()

    def test_reflection_called_on_failure(self, tmp_path: Path) -> None:
        """任务失败时也调用反思"""
        from agent_system.orchestrator import Orchestrator
        from agent_system.agents.coder import CodeChanges

        reviewer = MagicMock()
        reviewer.execute.return_value = ReviewResult(
            passed=False, issues=["fail"]
        )
        reflector = MagicMock()
        reflector.execute.return_value = ReflectionReport.from_dict({
            "task_id": "T-1",
            "task_title": "Test",
        })

        config = AgentConfig(dry_run=False)
        context = _make_context()

        orch = Orchestrator(
            config=config,
            planner=MagicMock(),
            analyst=MagicMock(execute=MagicMock(return_value="analysis")),
            coder=MagicMock(execute=MagicMock(
                return_value=CodeChanges(files=[])
            )),
            reviewer=reviewer,
            reflector=reflector,
            context=context,
        )
        orch._reflections_dir = tmp_path
        orch._state_store = MagicMock()
        orch._git = MagicMock()
        orch._git.has_changes.return_value = False
        orch._file_service = MagicMock()

        task = _make_task(TaskStatus.PENDING)
        task.status = TaskStatus.PENDING
        task.max_retries = 1
        orch.run_single_task(task)

        assert task.status == TaskStatus.FAILED
        reflector.execute.assert_called_once()

    def test_reflection_skipped_in_dry_run(self) -> None:
        """dry_run 模式下不调用反思"""
        from agent_system.orchestrator import Orchestrator

        config = AgentConfig(dry_run=True)
        context = _make_context()

        reflector = MagicMock()
        orch = Orchestrator(
            config=config,
            planner=MagicMock(),
            analyst=MagicMock(),
            coder=MagicMock(),
            reviewer=MagicMock(),
            reflector=reflector,
            context=context,
        )
        orch._reflections_dir = Path("/tmp/reflections")
        orch._state_store = MagicMock()

        task = _make_task(TaskStatus.DONE)
        orch._run_reflection(task)

        reflector.execute.assert_not_called()

    def test_reflection_failure_does_not_crash(self, tmp_path: Path) -> None:
        """反思异常不影响主流程"""
        from agent_system.orchestrator import Orchestrator
        from agent_system.agents.coder import CodeChanges

        reflector = MagicMock()
        reflector.execute.side_effect = RuntimeError("LLM down")

        config = AgentConfig(dry_run=False)
        context = _make_context()

        orch = Orchestrator(
            config=config,
            planner=MagicMock(),
            analyst=MagicMock(execute=MagicMock(return_value="ok")),
            coder=MagicMock(execute=MagicMock(
                return_value=CodeChanges(files=[])
            )),
            reviewer=MagicMock(execute=MagicMock(
                return_value=ReviewResult(passed=True)
            )),
            reflector=reflector,
            context=context,
        )
        orch._reflections_dir = tmp_path
        orch._state_store = MagicMock()
        orch._git = None
        orch._file_service = MagicMock()

        task = _make_task(TaskStatus.PENDING)
        task.status = TaskStatus.PENDING
        orch.run_single_task(task)

        # 反思失败但任务仍然成功
        assert task.status == TaskStatus.DONE
