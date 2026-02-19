"""Step 6 测试：Reviewer Agent + run_command 工具"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from agent_system.agents.coder import CodeChanges, FileChange
from agent_system.agents.reviewer import Reviewer
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import Task
from agent_system.tools.run_command import run_command_tool


class TestRunCommandTool:
    """run_command 工具测试"""

    def test_success_command(self) -> None:
        """执行成功命令 → stdout + exit_code=0"""
        if sys.platform == "win32":
            result = run_command_tool("echo hello")
        else:
            result = run_command_tool("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_failed_command(self) -> None:
        """执行失败命令 → exit_code!=0"""
        result = run_command_tool('python -c "raise Exception(\'fail\')"')
        assert result.exit_code != 0

    def test_timeout(self) -> None:
        """超时命令 → exit_code=-1"""
        if sys.platform == "win32":
            result = run_command_tool("ping -n 10 127.0.0.1", timeout=1)
        else:
            result = run_command_tool("sleep 10", timeout=1)
        assert result.exit_code == -1
        assert "超时" in result.stderr

    def test_success_property(self) -> None:
        """success 属性正确"""
        result = run_command_tool("echo ok")
        assert result.success is True


class TestReviewerAgent:
    """Reviewer Agent 测试"""

    def _make_config(self) -> ProjectConfig:
        return ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
            review_checklist=["无 any 类型", "编译通过", "无 console.log"],
            review_commands=["echo ok"],
        )

    def test_system_prompt_injection(self) -> None:
        """Reviewer prompt 注入 review_checklist"""
        mock_llm = MagicMock()
        reviewer = Reviewer(llm=mock_llm)
        config = self._make_config()
        prompt = reviewer.build_system_prompt(config)
        assert "无 any 类型" in prompt
        assert "编译通过" in prompt

    def test_reviewer_pass(self) -> None:
        """审查通过场景"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(
            content='{"passed": true, "issues": [], "suggestions": ["代码质量良好"]}'
        )

        reviewer = Reviewer(llm=mock_llm)
        config = self._make_config()
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="T1", title="Test", description="desc")
        changes = CodeChanges(files=[
            FileChange(path="test.ts", content="const x: number = 1;")
        ])

        result = reviewer.execute(task, ctx, code_changes=changes)
        assert result.passed is True

    def test_reviewer_fail_with_any(self) -> None:
        """审查失败：代码包含 any"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(
            content='{"passed": false, "issues": ["发现 any 类型使用"], "suggestions": ["替换为具体类型"]}'
        )

        reviewer = Reviewer(llm=mock_llm)
        config = self._make_config()
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="T1", title="Test", description="desc")
        changes = CodeChanges(files=[
            FileChange(path="test.ts", content="const x: any = 1;")
        ])

        result = reviewer.execute(task, ctx, code_changes=changes)
        assert result.passed is False
        assert any("any" in issue for issue in result.issues)

    def test_reviewer_command_failure(self) -> None:
        """reviewCommand 失败 → 自动标记为失败"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(
            content='{"passed": true, "issues": [], "suggestions": []}'
        )

        reviewer = Reviewer(llm=mock_llm)
        config = ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
            review_checklist=[],
            review_commands=['python -c "import sys; sys.exit(1)"'],
        )
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="T1", title="Test", description="desc")

        result = reviewer.execute(task, ctx)
        assert result.passed is False
        assert len(result.issues) > 0
