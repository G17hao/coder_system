"""测试: 任务失败/阻塞后暂停等待用户输入提示词"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import EmailApprovalConfig, ProjectConfig
from agent_system.models.task import Task, TaskStatus
from agent_system.agents.planner import DependencyStatus
from agent_system.orchestrator import Orchestrator
from agent_system.services.email_approval import EmailApprovalDecision


def _make_orchestrator(dry_run: bool = True) -> Orchestrator:
    """创建最小化 Orchestrator（无需真实 LLM）"""
    config = ProjectConfig(
        project_name="test",
        project_description="测试",
        project_root=".",
    )
    agent_config = AgentConfig(dry_run=dry_run)
    ctx = AgentContext(project=config, config=agent_config)
    ctx.task_queue = []

    mock_planner = MagicMock()
    orch = Orchestrator(
        config=agent_config,
        planner=mock_planner,
        analyst=MagicMock(),
        coder=MagicMock(),
        reviewer=MagicMock(),
        reflector=MagicMock(),
        supervisor=MagicMock(),
        context=ctx,
    )
    return orch


def _make_failed_task(status: TaskStatus = TaskStatus.FAILED) -> Task:
    task = Task(id="T1", title="测试任务", description="desc")
    task.status = status
    task.error = "审查未通过: some error"
    return task


class TestPromptUserHint:
    """_prompt_user_hint 单元测试"""

    def test_empty_input_returns_false(self) -> None:
        """用户直接回车（空输入）→ 返回 False，不继续"""
        orch = _make_orchestrator()
        task = _make_failed_task()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", return_value=""):
                result = orch._prompt_user_hint(task)

        assert result is False
        # 任务状态不变
        assert task.status == TaskStatus.FAILED

    def test_hint_input_returns_true_and_resets_task(self) -> None:
        """用户输入提示词 → 返回 True，任务重置为 PENDING，hint 已设置"""
        orch = _make_orchestrator()
        task = _make_failed_task()
        task.retry_count = 5

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", return_value="请修复第10行的类型错误"):
                result = orch._prompt_user_hint(task)

        assert result is True
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0
        assert task.error is None
        assert task.supervisor_hint == "请修复第10行的类型错误"

    def test_non_tty_returns_false(self) -> None:
        """非交互式环境（stdin 非 TTY）→ 自动返回 False"""
        orch = _make_orchestrator()
        task = _make_failed_task()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = orch._prompt_user_hint(task)

        assert result is False

    def test_eof_returns_false(self) -> None:
        """stdin 抛出 EOFError（如管道输入结束）→ 返回 False"""
        orch = _make_orchestrator()
        task = _make_failed_task()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", side_effect=EOFError):
                result = orch._prompt_user_hint(task)

        assert result is False

    def test_blocked_task_also_triggers_pause(self) -> None:
        """BLOCKED 任务也应触发暂停并可重置"""
        orch = _make_orchestrator()
        task = _make_failed_task(status=TaskStatus.BLOCKED)

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", return_value="绕过依赖，直接实现"):
                result = orch._prompt_user_hint(task)

        assert result is True
        assert task.status == TaskStatus.PENDING
        assert task.supervisor_hint == "绕过依赖，直接实现"

    def test_whitespace_only_input_treated_as_empty(self) -> None:
        """纯空白输入应视为空（不继续）"""
        orch = _make_orchestrator()
        task = _make_failed_task()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", return_value="   "):
                result = orch._prompt_user_hint(task)

        assert result is False


class TestRunPausesOnFailure:
    """run() 主循环在任务失败后暂停行为的集成测试"""

    def _make_run_orchestrator(
        self,
        task_statuses_after_run: list[TaskStatus],
    ) -> Orchestrator:
        """构造一个 run() 可执行的 Orchestrator：

        - planner.get_next_pending 依次返回任务，最终返回 None
        - run_single_task 被 mock，副作用是设置任务 status
        """
        config = ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
        )
        agent_config = AgentConfig(dry_run=True)
        ctx = AgentContext(project=config, config=agent_config)

        tasks = [
            Task(id=f"T{i}", title=f"任务{i}", description="desc")
            for i in range(len(task_statuses_after_run))
        ]
        ctx.task_queue = tasks

        mock_planner = MagicMock()
        # get_next_pending 依次返回各任务，最后返回 None
        mock_planner.get_next_pending.side_effect = tasks + [None]

        orch = Orchestrator(
            config=agent_config,
            planner=mock_planner,
            analyst=MagicMock(),
            coder=MagicMock(),
            reviewer=MagicMock(),
            reflector=MagicMock(),
            supervisor=MagicMock(),
            context=ctx,
        )

        # run_single_task 的副作用：设置对应任务状态
        def fake_run_single(task: Task) -> None:
            idx = tasks.index(task)
            task.status = task_statuses_after_run[idx]

        orch.run_single_task = MagicMock(side_effect=fake_run_single)  # type: ignore[method-assign]
        orch._save_state = MagicMock()  # type: ignore[method-assign]
        orch._print_report = MagicMock()  # type: ignore[method-assign]
        return orch

    def test_run_stops_when_user_gives_empty_input_after_failure(self) -> None:
        """任务 FAILED + 用户回车 → run() 停止，后续任务不执行"""
        orch = self._make_run_orchestrator([TaskStatus.FAILED, TaskStatus.DONE])

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", return_value=""):
                orch.run()

        # 第一个任务的 run_single_task 被调用，第二个不应被调用
        assert orch.run_single_task.call_count == 1  # type: ignore[attr-defined]

    def test_run_continues_when_user_provides_hint_after_failure(self) -> None:
        """任务 FAILED + 用户输入提示词 → 任务重置为 PENDING，继续执行"""
        tasks = [Task(id="T0", title="任务0", description="desc")]
        config = ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
        )
        agent_config = AgentConfig(dry_run=True)
        ctx = AgentContext(project=config, config=agent_config)
        ctx.task_queue = tasks

        mock_planner = MagicMock()
        call_count = {"n": 0}

        def get_next(context: AgentContext) -> Task | None:
            # 第1次返回任务，第2次（重置后）返回任务，第3次返回 None
            call_count["n"] += 1
            if call_count["n"] == 1:
                return tasks[0]
            if call_count["n"] == 2:
                return tasks[0]
            return None

        mock_planner.get_next_pending.side_effect = get_next

        run_count = {"n": 0}

        def fake_run_single(task: Task) -> None:
            run_count["n"] += 1
            if run_count["n"] == 1:
                task.status = TaskStatus.FAILED
            else:
                task.status = TaskStatus.DONE

        orch = Orchestrator(
            config=agent_config,
            planner=mock_planner,
            analyst=MagicMock(),
            coder=MagicMock(),
            reviewer=MagicMock(),
            reflector=MagicMock(),
            supervisor=MagicMock(),
            context=ctx,
        )
        orch.run_single_task = MagicMock(side_effect=fake_run_single)  # type: ignore[method-assign]
        orch._save_state = MagicMock()  # type: ignore[method-assign]
        orch._print_report = MagicMock()  # type: ignore[method-assign]

        input_calls = {"n": 0}

        def mock_input(prompt: str = "") -> str:
            input_calls["n"] += 1
            return "修复提示词" if input_calls["n"] == 1 else ""

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("builtins.input", side_effect=mock_input):
                orch.run()

        # 任务被执行两次（失败后重试）
        assert orch.run_single_task.call_count == 2  # type: ignore[attr-defined]
        assert tasks[0].status == TaskStatus.DONE


class TestEmailApprovalOnBlocked:
    """BLOCKED 状态下邮件审批控制测试"""

    def _make_blocked_orchestrator(self) -> Orchestrator:
        config = ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
            email_approval=EmailApprovalConfig(enabled=True),
        )
        agent_config = AgentConfig(dry_run=True)
        ctx = AgentContext(project=config, config=agent_config)
        orch = Orchestrator(
            config=agent_config,
            planner=MagicMock(),
            analyst=MagicMock(),
            coder=MagicMock(),
            reviewer=MagicMock(),
            reflector=MagicMock(),
            supervisor=MagicMock(),
            context=ctx,
        )
        return orch

    def test_blocked_continue_by_email(self) -> None:
        """邮件回复 CONTINUE 时，任务应重置为 pending 并继续"""
        orch = self._make_blocked_orchestrator()
        task = Task(id="T1", title="测试任务", description="desc", status=TaskStatus.BLOCKED)
        task.error = "[Supervisor] waiting"

        mock_email = MagicMock()
        mock_email.request_and_wait.return_value = EmailApprovalDecision(
            action="continue",
            hint="请优先修复类型不匹配",
            sender="user@example.com",
        )
        orch._email_approval = mock_email  # type: ignore[attr-defined]

        result = orch._handle_paused_task(task)

        assert result is True
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0
        assert task.error is None
        assert task.supervisor_hint == "请优先修复类型不匹配"
        assert mock_email.request_and_wait.call_count == 1
        _, kwargs = mock_email.request_and_wait.call_args
        assert "progress_summary" in kwargs
        assert "总任务" in kwargs["progress_summary"]

    def test_blocked_stop_by_email(self) -> None:
        """邮件回复 STOP 时，任务保持阻塞并停止主循环"""
        orch = self._make_blocked_orchestrator()
        task = Task(id="T2", title="测试任务2", description="desc", status=TaskStatus.BLOCKED)
        task.error = "[Supervisor] waiting"

        mock_email = MagicMock()
        mock_email.request_and_wait.return_value = EmailApprovalDecision(
            action="stop",
            sender="user@example.com",
        )
        orch._email_approval = mock_email  # type: ignore[attr-defined]

        result = orch._handle_paused_task(task)

        assert result is False
        assert task.status == TaskStatus.BLOCKED
        assert mock_email.request_and_wait.call_count == 1


class TestExitReason:
    """主循环退出原因输出测试"""

    def test_print_reason_when_pending_tasks_not_ready(self) -> None:
        pending_task = Task(
            id="T-P1",
            title="等待任务",
            description="desc",
            status=TaskStatus.PENDING,
            dependencies=["T-DONE-NEEDED"],
        )
        ctx = AgentContext(
            project=ProjectConfig(
                project_name="test",
                project_description="测试",
                project_root=".",
            ),
            config=AgentConfig(dry_run=True),
            task_queue=[pending_task],
        )

        planner = MagicMock()
        planner.get_next_pending.return_value = None
        planner.check_dependencies.return_value = DependencyStatus.BLOCKED

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=MagicMock(),
            coder=MagicMock(),
            reviewer=MagicMock(),
            reflector=MagicMock(),
            supervisor=MagicMock(),
            context=ctx,
        )
        orch._save_state = MagicMock()  # type: ignore[method-assign]
        orch._print_report = MagicMock()  # type: ignore[method-assign]

        with patch("builtins.print") as mock_print:
            orch.run()

        printed = "\n".join(
            str(call.args[0])
            for call in mock_print.call_args_list
            if call.args
        )
        assert "自动恢复" in printed
        assert "退出原因" in printed
        assert "无可执行任务" in printed
        assert "依赖摘要" in printed
