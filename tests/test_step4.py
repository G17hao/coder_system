"""Step 4 测试：Analyst Agent + 文件读取/搜索工具"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_system.agents.analyst import Analyst
from agent_system.agents.coder import CoderToolExecutor
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import PatternMapping, ProjectConfig
from agent_system.models.task import Task
from agent_system.tools.read_file import read_file_tool
from agent_system.tools.search_file import search_file_tool

FIXTURES = Path(__file__).parent / "fixtures"


class TestReadFileTool:
    """read_file 工具测试"""

    def test_read_full_file(self) -> None:
        """读取完整文件"""
        result = read_file_tool(str(FIXTURES / "sample.lua"))
        assert "function" in result
        assert "M.init" in result

    def test_read_line_range(self) -> None:
        """读取指定行范围"""
        result = read_file_tool(str(FIXTURES / "sample.lua"), start=1, end=5)
        assert "function" in result
        lines = result.strip().splitlines()
        assert len(lines) <= 5

    def test_read_nonexistent(self) -> None:
        """读取不存在的文件 → FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            read_file_tool("/nonexistent/file.lua")


class TestSearchFileTool:
    """search_file 工具测试"""

    def test_glob_search(self) -> None:
        """glob 模式搜索"""
        results = search_file_tool(str(FIXTURES), pattern="*.lua")
        assert len(results) >= 1
        assert any("sample.lua" in r for r in results)

    def test_glob_json(self) -> None:
        """搜索 JSON 文件"""
        results = search_file_tool(str(FIXTURES), pattern="*.json")
        assert len(results) >= 2  # valid_project.json + invalid_project.json

    def test_regex_filter(self) -> None:
        """正则过滤"""
        results = search_file_tool(str(FIXTURES), pattern="*", regex=r"sample")
        assert len(results) >= 1

    def test_nonexistent_dir(self) -> None:
        """搜索不存在的目录 → 空列表"""
        results = search_file_tool("/nonexistent/dir")
        assert results == []


class TestAnalystAgent:
    """Analyst Agent 集成测试（mock LLM）"""

    def test_analyst_with_mock_llm(self) -> None:
        """使用 mock LLM 返回预定义分析报告"""
        canned_report = (
            '{"interfaces": [{"name": "IPlayerModel"}], '
            '"methods": [{"name": "updateStats", "params": ["stats"]}], '
            '"events": ["StatsUpdate"], '
            '"files": ["src/models/PlayerModel.ts"], '
            '"gaps": ["缺少 PlayerModel 实现"]}'
        )
        mock_llm = MagicMock()
        # call_with_tools_loop 直接返回最终结果（不走真实工具循环）
        mock_llm.call_with_tools_loop.return_value = MagicMock(content=canned_report)

        analyst = Analyst(llm=mock_llm)

        config = ProjectConfig(
            project_name="test",
            project_description="测试项目",
            project_root="/tmp/project",
            reference_roots=["/tmp/reference"],
            pattern_mappings=[
                PatternMapping(from_pattern="View+Ctrl", to_pattern="Component+Service")
            ],
        )
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(
            id="T0.1",
            title="分析任务",
            description="分析网络包桥接层",
            category="infrastructure",
        )

        report = analyst.execute(task, ctx)
        assert "interfaces" in report
        assert "methods" in report
        assert "IPlayerModel" in report

    def test_analyst_builds_correct_user_message(self) -> None:
        """用户消息包含任务信息和项目路径"""
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(content="{}")
        analyst = Analyst(llm=mock_llm)

        config = ProjectConfig(
            project_name="myproject",
            project_description="desc",
            project_root="/project/root",
            reference_roots=["/ref1", "/ref2"],
        )
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(id="TX", title="Test", description="Test desc", category="model")

        analyst.execute(task, ctx)

        # 验证 call_with_tools_loop 被调用了
        mock_llm.call_with_tools_loop.assert_called_once()
        call_args = mock_llm.call_with_tools_loop.call_args
        messages = call_args.kwargs.get("messages") or call_args[1]["messages"] if len(call_args) > 1 else call_args.kwargs["messages"]
        user_content = messages[0]["content"]
        assert "TX" in user_content
        assert "/project/root" in user_content
        assert "/ref1" in user_content


class TestCoderToolExecutorTracking:
    """CoderToolExecutor 文件写入跟踪测试"""

    def test_write_file_tracked(self, tmp_path: Path) -> None:
        """write_file 调用应被自动跟踪"""
        executor = CoderToolExecutor()
        file_path = str(tmp_path / "test.ts")

        executor.execute("write_file", {"path": file_path, "content": "const x = 1;"})

        tracked = executor.tracked_changes
        assert len(tracked) == 1
        assert tracked[0]["path"] == file_path
        assert tracked[0]["content"] == "const x = 1;"
        assert tracked[0]["action"] == "create"

    def test_multiple_writes_tracked(self, tmp_path: Path) -> None:
        """多次写入不同文件都应被跟踪"""
        executor = CoderToolExecutor()
        f1 = str(tmp_path / "a.ts")
        f2 = str(tmp_path / "b.ts")

        executor.execute("write_file", {"path": f1, "content": "file a"})
        executor.execute("write_file", {"path": f2, "content": "file b"})

        tracked = executor.tracked_changes
        assert len(tracked) == 2

    def test_overwrite_same_file_keeps_latest(self, tmp_path: Path) -> None:
        """同一文件多次写入应保留最新内容"""
        executor = CoderToolExecutor()
        file_path = str(tmp_path / "test.ts")

        executor.execute("write_file", {"path": file_path, "content": "v1"})
        executor.execute("write_file", {"path": file_path, "content": "v2"})

        tracked = executor.tracked_changes
        assert len(tracked) == 1
        assert tracked[0]["content"] == "v2"

    def test_replace_in_file_tracked(self, tmp_path: Path) -> None:
        """replace_in_file 调用应被跟踪"""
        executor = CoderToolExecutor()
        file_path = str(tmp_path / "test.ts")
        # 先写入原始内容
        executor.execute("write_file", {"path": file_path, "content": "const x = 1;\nconst y = 2;\n"})
        # 用 replace 修改
        executor.execute("replace_in_file", {
            "path": file_path,
            "old_text": "const x = 1;",
            "new_text": "const x = 42;",
        })

        tracked = executor.tracked_changes
        assert len(tracked) == 1
        assert "const x = 42;" in tracked[0]["content"]
        assert tracked[0]["action"] == "modify"

    def test_no_writes_empty_tracked(self) -> None:
        """未写入任何文件时 tracked_changes 应为空"""
        executor = CoderToolExecutor()
        assert executor.tracked_changes == []
