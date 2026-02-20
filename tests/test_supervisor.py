"""Supervisor Agent + Orchestrator 集成测试"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_system.agents.analyst import Analyst
from agent_system.agents.coder import CodeChanges, FileChange, Coder
from agent_system.agents.planner import Planner
from agent_system.agents.reviewer import Reviewer
from agent_system.agents.supervisor import Supervisor, SupervisorDecision
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import ReviewResult, Task, TaskStatus
from agent_system.orchestrator import Orchestrator
from agent_system.services.state_store import StateStore


def _make_context(
    tasks: list[Task] | None = None,
    dry_run: bool = False,
) -> AgentContext:
    config = ProjectConfig(
        project_name="test",
        project_description="测试",
        project_root=".",
    )
    agent_config = AgentConfig(dry_run=dry_run)
    ctx = AgentContext(project=config, config=agent_config)
    ctx.task_queue = tasks or []
    return ctx


def _make_dummy_changes() -> CodeChanges:
    return CodeChanges(files=[
        FileChange(path="dummy.ts", content="// generated", action="create"),
    ])


class TestSupervisorAgent:
    """Supervisor Agent 单元测试"""

    def _make_supervisor(self, llm_content: str) -> Supervisor:
        mock_llm = MagicMock()
        mock_llm.call.return_value = MagicMock(content=llm_content)
        return Supervisor(llm=mock_llm)

    def test_parse_continue_decision(self) -> None:
        """LLM 返回 continue → 正确解析 action/reason/hint/extra_retries"""
        content = '{"action": "continue", "reason": "问题明确", "hint": "修改第10行", "extra_retries": 2}'
        supervisor = self._make_supervisor(content)
        task = Task(id="T1", title="test", description="desc", retry_count=5, max_retries=5)
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.action == "continue"
        assert decision.reason == "问题明确"
        assert decision.hint == "修改第10行"
        assert decision.extra_retries == 2

    def test_parse_halt_decision(self) -> None:
        """LLM 返回 halt → 正确解析"""
        content = (
            '{"action": "halt", "reason": "【证据】同样错误在多轮重试中重复出现。'
            '【阻塞】缺少关键接口定义导致无法继续收敛。'
            '【人工输入】请确认接口契约后再继续。", '
            '"hint": "", "extra_retries": 0}'
        )
        supervisor = self._make_supervisor(content)
        task = Task(id="T1", title="test", description="desc", retry_count=5, max_retries=5)
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.action == "halt"
        assert ("重复" in decision.reason) or ("反复" in decision.reason)

    def test_parse_fallback_on_invalid_json(self) -> None:
        """LLM 返回无法解析的内容 → 默认 halt"""
        supervisor = self._make_supervisor("这不是 JSON")
        task = Task(id="T1", title="test", description="desc", retry_count=3, max_retries=3)
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.action == "halt"

    def test_extra_retries_minimum_one(self) -> None:
        """extra_retries 不能为 0（至少为 1）"""
        content = '{"action": "continue", "reason": "ok", "hint": "", "extra_retries": 0}'
        supervisor = self._make_supervisor(content)
        task = Task(id="T1", title="test", description="desc")
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.extra_retries >= 1

    def test_json_embedded_in_text(self) -> None:
        """JSON 嵌在文本中也能解析"""
        content = '分析完成后结论是：\n{"action": "continue", "reason": "ok", "hint": "fix line 5", "extra_retries": 3}'
        supervisor = self._make_supervisor(content)
        task = Task(id="T1", title="test", description="desc")
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.action == "continue"
        assert decision.hint == "fix line 5"

    def test_parse_continue_with_structured_plan(self) -> None:
        """continue 决策可解析结构化重规划字段"""
        content = (
            '{"action":"continue","reason":"可修复","hint":"先修接口","extra_retries":2,'
            '"plan_summary":"先统一接口，再修调用方",'
            '"must_change_files":["a.ts","b.ts"],'
            '"execution_checklist":["修复接口","修复调用方"],'
            '"validation_steps":["npx tsc --noEmit"],'
            '"unknowns":["确认是否允许改公共接口"]}'
        )
        supervisor = self._make_supervisor(content)
        task = Task(id="T1", title="test", description="desc")
        ctx = _make_context()

        decision = supervisor.execute(task, ctx)

        assert decision.plan_summary == "先统一接口，再修调用方"
        assert decision.must_change_files == ["a.ts", "b.ts"]
        assert decision.execution_checklist == ["修复接口", "修复调用方"]
        assert decision.validation_steps == ["npx tsc --noEmit"]
        assert decision.unknowns == ["确认是否允许改公共接口"]

    def test_supervisor_hint_included_in_user_message(self) -> None:
        """任务有 supervisor_hint 时，user message 中包含上次提示"""
        mock_llm = MagicMock()
        mock_llm.call.return_value = MagicMock(
            content='{"action": "halt", "reason": "hint was ignored", "hint": "", "extra_retries": 0}'
        )
        supervisor = Supervisor(llm=mock_llm)
        task = Task(
            id="T1", title="test", description="desc",
            retry_count=5, max_retries=5,
            supervisor_hint="请修改 PlayerModel.ts 第 45 行",
        )
        ctx = _make_context()

        supervisor.execute(task, ctx)

        call_args = mock_llm.call.call_args
        user_message = call_args.kwargs.get("messages", call_args[0][1] if call_args[0] else [])[0]["content"]
        assert "请修改 PlayerModel.ts 第 45 行" in user_message


class TestOrchestratorSupervisorIntegration:
    """Orchestrator + Supervisor 集成测试"""

    def _make_orchestrator(
        self,
        tasks: list[Task],
        reviewer_results: list[ReviewResult],
        supervisor_decision: SupervisorDecision,
    ) -> Orchestrator:
        mock_llm = MagicMock()

        planner = Planner(llm=mock_llm)

        analyst = MagicMock(spec=Analyst)
        analyst.execute.return_value = '{"interfaces": []}'

        coder = MagicMock(spec=Coder)
        coder.execute.return_value = _make_dummy_changes()

        reviewer = MagicMock(spec=Reviewer)
        reviewer.execute.side_effect = reviewer_results + [ReviewResult(passed=True)] * 10

        supervisor = MagicMock(spec=Supervisor)
        supervisor.execute.return_value = supervisor_decision

        ctx = _make_context(tasks, dry_run=False)

        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            supervisor=supervisor,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()
        return orch

    def test_supervisor_called_when_retries_exhausted(self) -> None:
        """重试耗尽后 Supervisor 被调用"""
        task = Task(id="T0", title="Test", description="desc", max_retries=2)
        fail = ReviewResult(passed=False, issues=["error"])
        decision = SupervisorDecision(action="halt", reason="无法修复")

        orch = self._make_orchestrator([task], [fail, fail], decision)
        orch.run_single_task(task)

        assert orch._supervisor.execute.called  # type: ignore[union-attr]

    def test_supervisor_halt_sets_blocked(self) -> None:
        """Supervisor 决定 halt → 任务变为 BLOCKED"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["persistent error"])
        decision = SupervisorDecision(action="halt", reason="需要人工介入")

        orch = self._make_orchestrator([task], [fail], decision)
        orch.run_single_task(task)

        assert task.status == TaskStatus.BLOCKED
        assert "Supervisor" in (task.error or "")

    def test_supervisor_halt_persists_state_for_resume(self) -> None:
        """Supervisor halt 后应立即落盘，下次可从状态文件恢复"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["persistent error"])
        decision = SupervisorDecision(action="halt", reason="需要人工介入")

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "tasks.json"

            orch = self._make_orchestrator([task], [fail], decision)
            orch._state_store = StateStore(state_path)
            orch.run_single_task(task)

            loaded = StateStore(state_path).load()
            assert len(loaded) == 1
            assert loaded[0].id == "T0"
            assert loaded[0].status == TaskStatus.BLOCKED
            assert loaded[0].retry_count >= 1

    def test_supervisor_continue_adds_retries_and_succeeds(self) -> None:
        """Supervisor 决定 continue → 追加重试次数 → 最终通过"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["fixable error"])
        pass_result = ReviewResult(passed=True)
        decision = SupervisorDecision(
            action="continue",
            reason="问题可修复",
            hint="修改第 10 行类型定义",
            extra_retries=2,
        )

        mock_llm = MagicMock()
        planner = Planner(llm=mock_llm)
        analyst = MagicMock(spec=Analyst)
        analyst.execute.return_value = '{"interfaces": []}'
        coder = MagicMock(spec=Coder)
        coder.execute.return_value = _make_dummy_changes()
        reviewer = MagicMock(spec=Reviewer)
        # 第一次 fail → supervisor 介入 → 第二次 pass
        reviewer.execute.side_effect = [fail, pass_result]

        supervisor = MagicMock(spec=Supervisor)
        supervisor.execute.return_value = decision

        ctx = _make_context([task], dry_run=False)
        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            supervisor=supervisor,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()
        orch._git.commit.return_value = "abc123"

        orch.run_single_task(task)

        assert task.status == TaskStatus.DONE
        assert task.supervisor_hint == "修改第 10 行类型定义"

    def test_supervisor_hint_stored_on_task(self) -> None:
        """Supervisor continue 时 hint 存入 task.supervisor_hint"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["error"])
        decision = SupervisorDecision(
            action="continue",
            reason="ok",
            hint="具体修复提示",
            extra_retries=3,
        )

        mock_llm = MagicMock()
        planner = Planner(llm=mock_llm)
        analyst = MagicMock(spec=Analyst)
        analyst.execute.return_value = '{"interfaces": []}'
        coder = MagicMock(spec=Coder)
        coder.execute.return_value = _make_dummy_changes()
        reviewer = MagicMock(spec=Reviewer)
        # 所有审查都失败，让 supervisor continue 后最终 FAILED
        reviewer.execute.return_value = fail

        supervisor = MagicMock(spec=Supervisor)
        supervisor.execute.return_value = decision

        ctx = _make_context([task], dry_run=False)
        orch = Orchestrator(
            config=ctx.config,
            planner=planner,
            analyst=analyst,
            coder=coder,
            reviewer=reviewer,
            supervisor=supervisor,
            context=ctx,
        )
        orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
        orch._file_service = MagicMock()
        orch._git = MagicMock()

        orch.run_single_task(task)

        # hint 应被记录到 task
        assert task.supervisor_hint == "具体修复提示"

    def test_supervisor_continue_stores_replan_on_task(self) -> None:
        """Supervisor continue 时应把重规划文本落到 task.supervisor_plan"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["error"])
        decision = SupervisorDecision(
            action="continue",
            reason="ok",
            hint="先修复类型定义",
            extra_retries=2,
            plan_summary="先修接口，再跑编译",
            must_change_files=["core/a.ts"],
            execution_checklist=["修复 core/a.ts 类型", "修复调用方"],
            validation_steps=["npx tsc --noEmit"],
            unknowns=["确认接口边界"],
        )

        orch = self._make_orchestrator([task], [fail, ReviewResult(passed=True)], decision)
        orch._coder.execute.return_value = CodeChanges(files=[
            FileChange(path="core/a.ts", content="export const ok = true;", action="modify"),
        ])
        orch._git = MagicMock()
        orch._git.has_changes.return_value = False

        orch.run_single_task(task)

        assert task.status == TaskStatus.DONE
        assert task.supervisor_plan is not None
        assert "计划摘要: 先修接口，再跑编译" in task.supervisor_plan
        assert "必须修改文件:" in task.supervisor_plan
        assert "验证步骤:" in task.supervisor_plan

    def test_supervisor_only_intervenes_once(self) -> None:
        """Supervisor 每次任务执行中最多介入一次"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        fail = ReviewResult(passed=False, issues=["error"])
        decision = SupervisorDecision(
            action="continue", reason="ok", hint="hint", extra_retries=2
        )

        orch = self._make_orchestrator([task], [fail] * 10, decision)
        orch.run_single_task(task)

        assert orch._supervisor.execute.call_count == 1  # type: ignore[union-attr]

    def test_retry_fuse_triggers_supervisor_before_max_retries(self) -> None:
        """重试超过 3 次时触发熔断，不继续硬重试到 max_retries"""
        task = Task(id="T0", title="Test", description="desc", max_retries=10)
        fail = ReviewResult(passed=False, issues=["error"])
        decision = SupervisorDecision(action="halt", reason="进入根因分析")

        orch = self._make_orchestrator([task], [fail] * 10, decision)
        orch.run_single_task(task)

        assert orch._supervisor.execute.call_count == 1  # type: ignore[union-attr]
        assert orch._reviewer.execute.call_count == 4  # type: ignore[union-attr]
        assert task.status == TaskStatus.BLOCKED

    def test_alignment_check_only_adds_suggestion_when_key_files_missing(self) -> None:
        """审查通过但未覆盖分析关键文件时，仅追加建议，不阻断通过"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        task.analysis_cache = (
            '{"files":[{"path":"core/critical.ts","action":"modify"}],'
            '"gaps":["缺少 core/critical.ts 中关键逻辑"]}'
        )

        pass_result = ReviewResult(passed=True)
        decision = SupervisorDecision(action="halt", reason="缺口未覆盖")
        orch = self._make_orchestrator([task], [pass_result], decision)
        orch._git.has_changes.return_value = False

        # Coder 只改了 dummy.ts，不包含 analysis 关键文件
        orch.run_single_task(task)

        assert task.review_result is not None
        assert task.review_result.passed is True
        assert any("覆盖性校验" in suggestion for suggestion in task.review_result.suggestions)
        assert task.status == TaskStatus.DONE

    def test_alignment_check_allows_when_key_file_exists_in_workspace(self) -> None:
        """关键文件已存在于工作区时，不应因本轮未改动而阻断通过"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        task.analysis_cache = (
            '{"files":[{"path":"core/critical.ts","action":"modify"}],'
            '"gaps":["缺少 core/critical.ts 中关键逻辑"]}'
        )

        pass_result = ReviewResult(passed=True)
        decision = SupervisorDecision(action="halt", reason="不应触发")

        with tempfile.TemporaryDirectory() as tmp:
            critical = Path(tmp) / "core" / "critical.ts"
            critical.parent.mkdir(parents=True, exist_ok=True)
            critical.write_text("export const ok = true;", encoding="utf-8")

            mock_llm = MagicMock()
            planner = Planner(llm=mock_llm)
            analyst = MagicMock(spec=Analyst)
            analyst.execute.return_value = '{"interfaces": []}'
            coder = MagicMock(spec=Coder)
            coder.execute.return_value = _make_dummy_changes()
            reviewer = MagicMock(spec=Reviewer)
            reviewer.execute.return_value = pass_result
            supervisor = MagicMock(spec=Supervisor)
            supervisor.execute.return_value = decision

            config = ProjectConfig(
                project_name="test",
                project_description="测试",
                project_root=tmp,
            )
            agent_config = AgentConfig(dry_run=False)
            ctx = AgentContext(project=config, config=agent_config)
            ctx.task_queue = [task]

            orch = Orchestrator(
                config=ctx.config,
                planner=planner,
                analyst=analyst,
                coder=coder,
                reviewer=reviewer,
                supervisor=supervisor,
                context=ctx,
            )
            orch._state_store = StateStore(Path(tempfile.mktemp(suffix=".json")))
            orch._file_service = MagicMock()
            orch._git = MagicMock()
            orch._git.has_changes.return_value = False

            orch.run_single_task(task)

            assert task.status == TaskStatus.DONE
            assert task.review_result is not None
            assert task.review_result.passed is True
            assert not supervisor.execute.called

    def test_must_change_files_reconcile_blocks_before_reviewer(self) -> None:
        """Supervisor 指定 must_change_files 未覆盖时，应先对账失败并跳过 Reviewer"""
        task = Task(id="T0", title="Test", description="desc", max_retries=1)
        task.supervisor_must_change_files = ["core/must-fix.ts"]

        pass_result = ReviewResult(passed=True)
        decision = SupervisorDecision(action="halt", reason="对账未通过")
        orch = self._make_orchestrator([task], [pass_result], decision)

        orch.run_single_task(task)

        assert task.review_result is not None
        assert task.review_result.passed is False
        assert any("must_change_files" in issue for issue in task.review_result.issues)
        assert orch._reviewer.execute.call_count == 0  # type: ignore[union-attr]
        assert task.status == TaskStatus.BLOCKED
