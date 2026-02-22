"""Step 5 测试：Coder Agent + write_file 工具"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_system.agents.coder import Coder, CodeChanges
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import PatternMapping, ProjectConfig
from agent_system.models.task import Task
from agent_system.tools.write_file import write_file_tool


class TestWriteFileTool:
    """write_file 工具测试"""

    def test_write_file(self) -> None:
        """写入文件 → 文件存在且内容正确"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ts"
            write_file_tool(path, "const x = 1;")
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "const x = 1;"

    def test_create_intermediate_dirs(self) -> None:
        """自动创建中间目录"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "c.ts"
            write_file_tool(path, "export {}")
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "export {}"

    def test_overwrite_existing(self) -> None:
        """覆盖已存在的文件"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ts"
            path.write_text("old content", encoding="utf-8")
            write_file_tool(path, "new content")
            assert path.read_text(encoding="utf-8") == "new content"


class TestCodeChanges:
    """CodeChanges 数据结构测试"""

    def test_from_json(self) -> None:
        """从 JSON 解析文件变更"""
        json_str = (
            '{"files": [{"path": "src/a.ts", "content": "const a = 1;", "action": "create"}], '
            '"review_files": ["src/a.ts", "src/b.ts"]}'
        )
        changes = CodeChanges.from_json(json_str)
        assert len(changes.files) == 1
        assert changes.files[0].path == "src/a.ts"
        assert changes.files[0].content == "const a = 1;"
        assert changes.review_files == ["src/a.ts", "src/b.ts"]

    def test_from_json_without_content(self) -> None:
        """新格式：仅 path/action 也可解析"""
        json_str = '{"files": [{"path": "src/a.ts", "action": "modify"}]}'
        changes = CodeChanges.from_json(json_str)
        assert len(changes.files) == 1
        assert changes.files[0].path == "src/a.ts"
        assert changes.files[0].content is None

    def test_from_json_with_surrounding_text(self) -> None:
        """从包含额外文本的 LLM 输出中提取 JSON"""
        llm_output = (
            'Here is the result:\n\n'
            '{"files": [{"path": "test.ts", "content": "hello"}]}\n\n'
            'Done!'
        )
        changes = CodeChanges.from_json(llm_output)
        assert len(changes.files) == 1
        assert changes.files[0].path == "test.ts"

    def test_from_json_invalid(self) -> None:
        """无效 JSON → 空列表"""
        changes = CodeChanges.from_json("no json here")
        assert len(changes.files) == 0


class TestCoderAgent:
    """Coder Agent 测试"""

    def _make_config(self) -> ProjectConfig:
        return ProjectConfig(
            project_name="test",
            project_description="测试项目",
            project_root="/tmp/project",
            reference_roots=["/tmp/ref"],
            coding_conventions="禁止 any 类型\n使用 interface 定义契约",
            pattern_mappings=[
                PatternMapping(from_pattern="View+Ctrl", to_pattern="Component+Service")
            ],
            review_checklist=["无 any 类型", "通过 tsc 编译"],
            review_commands=["npx tsc --noEmit", "npx vitest run"],
        )

    def test_system_prompt_injection(self) -> None:
        """Coder prompt 模板注入 coding_conventions"""
        mock_llm = MagicMock()
        coder = Coder(llm=mock_llm)
        config = self._make_config()
        prompt = coder.build_system_prompt(config)
        assert "禁止 any 类型" in prompt
        assert "View+Ctrl" in prompt
        assert "无 any 类型" in prompt
        assert "npx tsc --noEmit" in prompt

    def test_coder_integration(self) -> None:
        """Coder 集成测试（mock LLM 返回预定义文件内容）"""
        canned_response = (
            '{"files": ['
            '{"path": "src/models/PlayerModel.ts", "action": "create", '
            '"content": "export class PlayerModel {\\n  private _hp: number = 0;\\n}"}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.call_with_tools_loop.return_value = MagicMock(content=canned_response)

        coder = Coder(llm=mock_llm)
        config = self._make_config()
        ctx = AgentContext(project=config, config=AgentConfig())
        task = Task(
            id="T0.1",
            title="创建 PlayerModel",
            description="创建玩家数据模型",
            category="model",
        )

        changes = coder.execute(task, ctx, analysis_report='{"interfaces": []}')
        assert len(changes.files) > 0
        assert all(f.path for f in changes.files)
        assert all(f.action in ("create", "modify", "delete") for f in changes.files)
