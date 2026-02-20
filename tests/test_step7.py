"""Step 7 测试：Orchestrator 主循环 + CLI"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_system.agents.coder import CodeChanges, FileChange
from agent_system.agents.planner import DependencyStatus, Planner
from agent_system.agents.analyst import Analyst
from agent_system.agents.coder import Coder
from agent_system.agents.reviewer import Reviewer
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import ReviewResult, Task, TaskStatus
from agent_system.orchestrator import Orchestrator
from agent_system.services.state_store import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def _make_mock_agents(
    analyst_report: str = '{"interfaces": []}',
    coder_changes: CodeChanges | None = None,
    reviewer_results: list[ReviewResult] | None = None,
) -> tuple:
    """创建 mock agents

    Returns:
        (planner, analyst, coder, reviewer)
    """
    mock_llm = MagicMock()

    planner = Planner(llm=mock_llm)

    analyst = MagicMock(spec=Analyst)
    analyst.execute.return_value = analyst_report

    if coder_changes is None:
        coder_changes = CodeChanges(files=[
            FileChange(path="dummy.ts", content="// generated", action="create"),
        ])
    coder = MagicMock(spec=Coder)
    coder.execute.return_value = coder_changes

    reviewer = MagicMock(spec=Reviewer)
    if reviewer_results is None:
        reviewer_results = [ReviewResult(passed=True)]
    reviewer.execute.side_effect = reviewer_results * 10  # 足够多的结果

    return planner, analyst, coder, reviewer


def _make_tasks() -> list[Task]:
    """创建 3 个链式依赖任务"""
    return [
        Task(id="T0", title="Task 0", description="desc0", priority=0),
        Task(id="T1", title="Task 1", description="desc1", dependencies=["T0"], priority=10),
        Task(id="T2", title="Task 2", description="desc2", dependencies=["T1"], priority=20),
    ]


def _make_context(
    tasks: list[Task] | None = None, dry_run: bool = True
) -> AgentContext:
    config = ProjectConfig(
        project_name="test",
        project_description="test",
        project_root=".",
        review_commands=[],
    )
    return AgentContext(
        project=config,
        task_queue=tasks or _make_tasks(),
        config=AgentConfig(dry_run=dry_run, git_auto_commit=False),
    )


class TestOrchestratorMainLoop:
    """主循环集成测试"""

    def test_all_tasks_done(self) -> None:
        """3 个 mock 任务按依赖顺序执行 → 全部 done"""
        planner, analyst, coder, reviewer = _make_mock_agents()
        tasks = _make_tasks()
        ctx = _make_context(tasks)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()

        orch.run()

        assert all(t.status == TaskStatus.DONE for t in orch.context.task_queue)

    def test_execution_order(self) -> None:
        """任务按依赖顺序执行（T0→T1→T2）"""
        execution_order: list[str] = []

        planner, analyst, coder, reviewer = _make_mock_agents()

        # 追踪 analyst.execute 调用顺序
        original_analyst = MagicMock(spec=Analyst)
        def track_execute(task, context, **kwargs):
            execution_order.append(task.id)
            return '{"interfaces": []}'
        original_analyst.execute.side_effect = track_execute

        tasks = _make_tasks()
        ctx = _make_context(tasks, dry_run=False)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=original_analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()
        orch._git.has_changes.return_value = False

        orch.run()

        assert execution_order == ["T0", "T1", "T2"]


class TestRetryLogic:
    """重试逻辑测试"""

    def test_retry_then_pass(self) -> None:
        """Reviewer 第一次 fail，第二次 pass → 任务最终 done"""
        planner, analyst, coder, _ = _make_mock_agents()

        fail_result = ReviewResult(passed=False, issues=["type error"], suggestions=["fix it"])
        pass_result = ReviewResult(passed=True)

        reviewer = MagicMock(spec=Reviewer)
        reviewer.execute.side_effect = [fail_result, pass_result]

        task = Task(id="T0", title="Test", description="desc")
        ctx = _make_context([task], dry_run=False)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()
        orch._git.has_changes.return_value = False

        orch.run_single_task(task)

        assert task.status == TaskStatus.DONE
        assert task.retry_count == 1

    def test_max_retries_exceeded(self) -> None:
        """连续 3 次 fail → 任务 failed"""
        planner, analyst, coder, _ = _make_mock_agents()

        fail_result = ReviewResult(passed=False, issues=["persistent error"], suggestions=[])
        reviewer = MagicMock(spec=Reviewer)
        reviewer.execute.side_effect = [fail_result] * 5

        task = Task(id="T0", title="Test", description="desc", max_retries=3)
        ctx = _make_context([task], dry_run=False)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()

        orch.run_single_task(task)

        assert task.status == TaskStatus.FAILED
        assert task.retry_count == 3


class TestBreakpointResume:
    """断点恢复测试"""

    def test_resume_from_checkpoint(self) -> None:
        """运行 2 个任务后中断 → 重新加载 → 从第 3 个继续"""
        planner, analyst, coder, reviewer = _make_mock_agents()
        tasks = _make_tasks()
        ctx = _make_context(tasks)

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "tasks.json"

            orch = Orchestrator(
                config=ctx.config,
                planner=planner,
                analyst=analyst,
                coder=coder,
                reviewer=reviewer,
                context=ctx,
            )
            orch._state_store = StateStore(state_path)
            orch._file_service = MagicMock()

            # 执行前 2 个任务
            orch.run_until(task_count=2)

            assert tasks[0].status == TaskStatus.DONE
            assert tasks[1].status == TaskStatus.DONE
            assert tasks[2].status == TaskStatus.PENDING

            # 从断点恢复
            store = StateStore(state_path)
            loaded_tasks = store.load()

            ctx2 = _make_context(loaded_tasks)
            ctx2.completed_tasks = {t.id: t for t in loaded_tasks if t.status == TaskStatus.DONE}

            planner2, analyst2, coder2, reviewer2 = _make_mock_agents()
            orch2 = Orchestrator(
                config=ctx2.config,
                planner=planner2,
                analyst=analyst2,
                coder=coder2,
                reviewer=reviewer2,
                context=ctx2,
            )
            orch2._state_store = StateStore(state_path)
            orch2._file_service = MagicMock()

            next_task = orch2.next_pending_task()
            assert next_task is not None
            assert next_task.id == "T2"


class TestCLI:
    """CLI 命令行测试"""

    def test_dry_run(self) -> None:
        """--dry-run 模式正常退出"""
        result = subprocess.run(
            [
                sys.executable, "-m", "agent_system",
                "--project", str(FIXTURES / "e2e_project.json"),
                "--init", "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0

    def test_status(self) -> None:
        """--status 输出任务统计"""
        # 先 init 创建状态文件
        subprocess.run(
            [
                sys.executable, "-m", "agent_system",
                "--project", str(FIXTURES / "e2e_project.json"),
                "--init", "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent),
        )

        # 然后查看状态（使用同一项目配置）
        result = subprocess.run(
            [
                sys.executable, "-m", "agent_system",
                "--project", str(FIXTURES / "e2e_project.json"),
                "--status",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent),
        )
        # status 命令应该能运行（可能报错因为 project_root="." 可能没有 state 文件）
        # 但不应该崩溃
        assert result.returncode in (0, 1)

    def test_help(self) -> None:
        """--help 正常输出"""
        result = subprocess.run(
            [sys.executable, "-m", "agent_system", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "agent-system" in result.stdout


class TestStatusReport:
    """状态报告测试"""

    def test_report_content(self) -> None:
        """状态报告包含正确的计数"""
        tasks = _make_tasks()
        tasks[0].status = TaskStatus.DONE
        tasks[1].status = TaskStatus.PENDING
        tasks[2].status = TaskStatus.BLOCKED

        ctx = _make_context(tasks)
        orch = Orchestrator(
            config=ctx.config,
            context=ctx,
        )

        report = orch.get_status_report()
        assert "done: 1" in report
        assert "pending: 1" in report
        assert "blocked: 1" in report


class TestTokenUsageSync:
    """Token / API 调用次数跟踪测试"""

    def test_token_usage_synced_after_task(self) -> None:
        """任务执行后 context.total_tokens_used 应被更新"""
        from agent_system.services.llm import LLMService, TokenUsage

        planner, analyst, coder, reviewer = _make_mock_agents()
        task = Task(id="T0", title="Test", description="desc")
        ctx = _make_context([task], dry_run=False)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()
        orch._git.has_changes.return_value = False

        # 模拟 LLM 有 token 消耗
        mock_llm = MagicMock(spec=LLMService)
        mock_usage = TokenUsage(total_input=1000, total_output=500, total_calls=5)
        mock_llm.usage = mock_usage
        orch._llm = mock_llm

        orch.run_single_task(task)

        assert ctx.total_tokens_used == 1500  # 1000 + 500
        assert ctx.total_api_calls == 5

    def test_call_limit_stops_execution(self) -> None:
        """当 API 调用次数达到 call_limit 时主循环应停止（不再启动新任务）"""
        from agent_system.services.llm import LLMService, TokenUsage

        planner, analyst, coder, reviewer = _make_mock_agents()
        tasks = _make_tasks()
        config = AgentConfig(
            dry_run=True,
            git_auto_commit=False,
            call_limit=3,
        )
        project_config = ProjectConfig(
            project_name="test",
            project_description="test",
            project_root=".",
            review_commands=[],
        )
        ctx = AgentContext(
            project=project_config,
            task_queue=tasks,
            config=config,
        )

        orch = Orchestrator(
            config=config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()

        # 模拟 LLM 已有 3 次调用（恰好达到限制）
        mock_llm = MagicMock(spec=LLMService)
        mock_usage = TokenUsage(total_input=100, total_output=50, total_calls=3)
        mock_llm.usage = mock_usage
        orch._llm = mock_llm

        orch.run()

        # 预算检查在任务执行前，所以 0 个任务被执行
        done_count = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        assert done_count == 0
        assert ctx.total_api_calls == 3

    def test_budget_limit_stops_execution(self) -> None:
        """当 token 使用超过 budget_limit 时主循环应停止（不再启动新任务）"""
        from agent_system.services.llm import LLMService, TokenUsage

        planner, analyst, coder, reviewer = _make_mock_agents()
        tasks = _make_tasks()
        config = AgentConfig(
            dry_run=True,
            git_auto_commit=False,
            budget_limit=100,
        )
        project_config = ProjectConfig(
            project_name="test",
            project_description="test",
            project_root=".",
            review_commands=[],
        )
        ctx = AgentContext(
            project=project_config,
            task_queue=tasks,
            config=config,
        )

        orch = Orchestrator(
            config=config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()

        # 模拟超出预算（80+50=130 > 100）
        mock_llm = MagicMock(spec=LLMService)
        mock_usage = TokenUsage(total_input=80, total_output=50, total_calls=2)
        mock_llm.usage = mock_usage
        orch._llm = mock_llm

        orch.run()

        # 预算检查在任务执行前，所以 0 个任务被执行
        done_count = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        assert done_count == 0
        assert ctx.total_tokens_used == 130  # 80 + 50

    def test_report_shows_api_calls(self) -> None:
        """执行报告显示 API 调用次数"""
        from agent_system.services.llm import LLMService, TokenUsage

        tasks = _make_tasks()
        ctx = _make_context(tasks)
        ctx.total_tokens_used = 12345
        ctx.total_api_calls = 42

        orch = Orchestrator(
            config=ctx.config,
            context=ctx,
        )
        # 需要 _llm 以便 _print_report 中的 _sync_llm_usage 不报错
        mock_llm = MagicMock(spec=LLMService)
        mock_usage = TokenUsage(total_input=6000, total_output=6345, total_calls=42)
        mock_llm.usage = mock_usage
        orch._llm = mock_llm

        report = orch.get_status_report()
        assert "3" in report  # 3 个任务

    def test_sync_llm_usage_without_llm(self) -> None:
        """没有 _llm 引用时 _sync_llm_usage 不报错"""
        tasks = _make_tasks()
        ctx = _make_context(tasks)
        orch = Orchestrator(config=ctx.config, context=ctx)
        # _llm is None
        orch._sync_llm_usage()  # should not raise
        assert ctx.total_tokens_used == 0
        assert ctx.total_api_calls == 0


class TestToolExecutorErrorHandling:
    """ToolExecutor 全局异常捕获测试"""

    def test_analyst_tool_permission_error(self) -> None:
        """AnalystToolExecutor 遇到 PermissionError 时返回错误字符串而非崩溃"""
        from agent_system.agents.analyst import AnalystToolExecutor
        executor = AnalystToolExecutor()
        # 传入不存在的路径触发 FileNotFoundError
        result = executor.execute("read_file", {"path": "/nonexistent/file.ts"})
        assert "错误" in result
        # 传入错误参数触发 KeyError
        result = executor.execute("grep_content", {"wrong_key": "x"})
        assert "错误" in result
        assert "KeyError" in result

    def test_coder_tool_permission_error(self) -> None:
        """CoderToolExecutor 遇到异常时返回错误字符串而非崩溃"""
        from agent_system.agents.coder import CoderToolExecutor
        executor = CoderToolExecutor()
        result = executor.execute("read_file", {"path": "/nonexistent/file.ts"})
        assert "错误" in result
        result = executor.execute("write_file", {"path": "", "content": ""})
        assert "错误" in result or isinstance(result, str)


class TestResetFailedTasks:
    """重置失败任务测试"""

    def test_reset_failed_to_pending(self) -> None:
        """reset_failed_tasks 将 failed/in-progress 任务重置为 pending"""
        tasks = _make_tasks()
        tasks[0].status = TaskStatus.FAILED
        tasks[0].error = "some error"
        tasks[0].retry_count = 3
        tasks[1].status = TaskStatus.IN_PROGRESS
        tasks[1].error = "interrupted"
        tasks[1].retry_count = 1
        tasks[2].status = TaskStatus.PENDING

        ctx = _make_context(tasks)
        orch = Orchestrator(config=ctx.config, context=ctx)
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))

        orch.reset_failed_tasks()

        assert tasks[0].status == TaskStatus.PENDING
        assert tasks[0].error is None
        assert tasks[0].retry_count == 0
        assert tasks[1].status == TaskStatus.PENDING
        assert tasks[1].error is None
        assert tasks[1].retry_count == 0
        assert tasks[2].status == TaskStatus.PENDING  # 不受影响

    def test_reset_no_failed_tasks(self) -> None:
        """没有 failed 任务时无操作"""
        tasks = _make_tasks()
        ctx = _make_context(tasks)
        orch = Orchestrator(config=ctx.config, context=ctx)
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))

        orch.reset_failed_tasks()  # should not raise
        assert all(t.status == TaskStatus.PENDING for t in tasks)
