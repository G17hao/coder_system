"""Step 6 测试：Reviewer Agent + run_command 工具"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
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
        """reviewCommand 通过 LLM 工具循环执行，验证 commands 和 run_command 工具传入"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(
            content='{"passed": false, "issues": ["编译失败"], "suggestions": ["修复类型错误"]}'
        )

        reviewer = Reviewer(llm=mock_llm)
        config = ProjectConfig(
            project_name="test",
            project_description="测试",
            project_root=".",
            review_checklist=[],
            review_commands=['npx tsc --noEmit', 'npx vitest run'],
        )
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="T1", title="Test", description="desc")

        # 必须传入 code_changes，否则新逻辑会自动 PASS
        changes = CodeChanges(files=[
            FileChange(path="test.ts", content="const x: number = 1;", action="create")
        ])
        result = reviewer.execute(task, ctx, code_changes=changes)

        # LLM 应该收到包含 review commands 的 user_message
        call_args = mock_llm.call_with_tools_loop.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][1]
        user_msg = messages[0]["content"]
        assert "npx tsc --noEmit" in user_msg
        assert "npx vitest run" in user_msg
        assert "run_command" in user_msg

        # run_command 工具应该在 tools 列表中
        tools = call_args.kwargs.get("tools") or call_args[1].get("tools") or call_args[0][2]
        tool_names = [t["name"] for t in tools]
        assert "run_command" in tool_names

        # LLM 返回失败时，结果应该是失败
        assert result.passed is False
        assert len(result.issues) > 0

    def test_reviewer_auto_pass_no_changes(self) -> None:
        """Coder 无文件产出时，reviewer 应自动 PASS（不调用 LLM）"""
        mock_llm = MagicMock()
        reviewer = Reviewer(llm=mock_llm)
        config = self._make_config()
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="T1", title="Test", description="desc")

        # code_changes=None
        result = reviewer.execute(task, ctx, code_changes=None)
        assert result.passed is True
        mock_llm.call_with_tools_loop.assert_not_called()

        # code_changes 有空文件列表
        empty_changes = CodeChanges(files=[])
        result2 = reviewer.execute(task, ctx, code_changes=empty_changes)
        assert result2.passed is True

    def test_reviewer_preloads_disk_content_without_inline_content(self) -> None:
        """CodeChanges 无 content 时，Reviewer 仍应从磁盘预加载代码注入 user_message"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(
            content='{"passed": true, "issues": [], "suggestions": []}'
        )

        reviewer = Reviewer(llm=mock_llm)
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.ts"
            file_path.write_text("const preloaded = 123;", encoding="utf-8")

            config = ProjectConfig(
                project_name="test",
                project_description="测试",
                project_root=tmp,
                review_checklist=[],
                review_commands=[],
            )
            ctx = AgentContext(project=config, config=AgentConfig())
            task = Task(id="T1", title="Test", description="desc")
            changes = CodeChanges(files=[
                FileChange(path="test.ts", action="modify")
            ])

            reviewer.execute(task, ctx, code_changes=changes)

            call_args = mock_llm.call_with_tools_loop.call_args
            messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][1]
            user_msg = messages[0]["content"]
            assert "系统已从磁盘预加载" in user_msg
            assert "const preloaded = 123;" in user_msg
