"""Step 10: ConversationLogger 与 LLM 进度日志测试"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_system.services.conversation_logger import (
    ConversationEntry,
    ConversationLog,
    ConversationLogger,
    load_conversation,
    list_task_conversations,
)


# ── ConversationEntry ──────────────────────────────────────────

class TestConversationEntry:
    """ConversationEntry 测试"""

    def test_to_dict(self) -> None:
        entry = ConversationEntry(role="user", content="hello")
        d = entry.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert "timestamp" in d

    def test_custom_timestamp(self) -> None:
        entry = ConversationEntry(role="assistant", content="hi", timestamp="2026-01-01T00:00:00")
        assert entry.timestamp == "2026-01-01T00:00:00"


# ── ConversationLog ───────────────────────────────────────────

class TestConversationLog:
    """ConversationLog 测试"""

    def test_add_system(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="analyst")
        log.add_system("You are an analyst")
        assert log.system_prompt == "You are an analyst"

    def test_add_user(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="analyst")
        log.add_user("Analyze this code")
        assert len(log.entries) == 1
        assert log.entries[0].role == "user"

    def test_add_assistant(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="analyst")
        log.add_assistant("Here is my analysis", tool_calls=[{"id": "1", "name": "read_file", "input": {}}])
        assert len(log.entries) == 1
        assert log.tool_calls_count == 1

    def test_add_tool_result(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="analyst")
        log.add_tool_result("tool-1", "read_file", "file contents here")
        assert len(log.entries) == 1
        assert log.entries[0].role == "tool_result"

    def test_add_token_usage(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="coder")
        log.add_token_usage(100, 200)
        log.add_token_usage(50, 100)
        assert log.token_usage["input_tokens"] == 150
        assert log.token_usage["output_tokens"] == 300
        assert log.iterations == 2

    def test_finish(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="coder")
        assert log.finished_at is None
        log.finish()
        assert log.finished_at is not None

    def test_to_dict(self) -> None:
        log = ConversationLog(task_id="T-1", agent_name="reviewer")
        log.add_system("system prompt")
        log.add_user("review this")
        log.add_assistant("looks good")
        log.add_token_usage(50, 100)
        log.finish()

        d = log.to_dict()
        assert d["task_id"] == "T-1"
        assert d["agent_name"] == "reviewer"
        assert d["system_prompt"] == "system prompt"
        assert len(d["entries"]) == 2
        assert d["token_usage"]["input_tokens"] == 50
        assert d["iterations"] == 1
        assert d["finished_at"] is not None

    def test_tool_result_truncation(self) -> None:
        """超长工具结果截断到 5000 字符"""
        log = ConversationLog(task_id="T-1", agent_name="analyst")
        long_result = "x" * 10000
        log.add_tool_result("t1", "read_file", long_result)
        saved = log.entries[0].content["result"]
        assert len(saved) == 5000


# ── ConversationLogger ─────────────────────────────────────────

class TestConversationLogger:
    """ConversationLogger 持久化测试"""

    def test_start_and_save(self, tmp_path: Path) -> None:
        """启动并保存对话"""
        cl = ConversationLogger(tmp_path)
        log = cl.start("T-1", "analyst")
        log.add_system("system")
        log.add_user("user message")
        log.add_assistant("response")
        log.add_token_usage(100, 200)

        filepath = cl.finish_and_save()
        assert filepath is not None
        assert filepath.exists()

        # 验证目录结构
        assert filepath.parent.name == "T-1"
        assert "analyst" in filepath.name
        assert filepath.suffix == ".json"

        # 验证文件内容
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["task_id"] == "T-1"
        assert data["agent_name"] == "analyst"
        assert len(data["entries"]) == 2

    def test_save_without_start(self, tmp_path: Path) -> None:
        """无活跃对话时 save 返回 None"""
        cl = ConversationLogger(tmp_path)
        assert cl.finish_and_save() is None

    def test_discard(self, tmp_path: Path) -> None:
        """丢弃对话"""
        cl = ConversationLogger(tmp_path)
        cl.start("T-1", "coder")
        cl.discard()
        assert cl.active_log is None
        assert cl.finish_and_save() is None

    def test_special_chars_in_task_id(self, tmp_path: Path) -> None:
        """任务 ID 含斜杠时安全处理"""
        cl = ConversationLogger(tmp_path)
        log = cl.start("phase1/task-01", "analyst")
        log.add_user("test")
        filepath = cl.finish_and_save()
        assert filepath is not None
        assert filepath.exists()
        assert "/" not in filepath.parent.name

    def test_multiple_conversations(self, tmp_path: Path) -> None:
        """同一任务多次对话"""
        cl = ConversationLogger(tmp_path)

        for agent in ["analyst", "coder", "reviewer"]:
            log = cl.start("T-1", agent)
            log.add_user(f"{agent} message")
            log.add_assistant(f"{agent} response")
            log.add_token_usage(10, 20)
            cl.finish_and_save()

        files = list_task_conversations(tmp_path, "T-1")
        assert len(files) == 3


# ── load / list helpers ────────────────────────────────────────

class TestConversationHelpers:
    """辅助函数测试"""

    def test_load_conversation(self, tmp_path: Path) -> None:
        """加载对话文件"""
        cl = ConversationLogger(tmp_path)
        log = cl.start("T-1", "analyst")
        log.add_user("hello")
        filepath = cl.finish_and_save()
        assert filepath is not None

        data = load_conversation(filepath)
        assert data["task_id"] == "T-1"
        assert len(data["entries"]) == 1

    def test_load_nonexistent(self) -> None:
        """文件不存在"""
        data = load_conversation("/no/file.json")
        assert "error" in data

    def test_list_task_conversations(self, tmp_path: Path) -> None:
        """列出任务对话"""
        cl = ConversationLogger(tmp_path)
        for agent in ["analyst", "coder"]:
            log = cl.start("T-2", agent)
            log.add_user("test")
            cl.finish_and_save()

        files = list_task_conversations(tmp_path, "T-2")
        assert len(files) == 2

    def test_list_nonexistent_task(self, tmp_path: Path) -> None:
        """任务不存在"""
        files = list_task_conversations(tmp_path, "T-999")
        assert files == []


# ── LLMService 进度日志集成 ─────────────────────────────────────

class TestLLMConversationLogging:
    """LLM call 记录到 ConversationLog"""

    def test_call_records_to_log(self) -> None:
        """call() 将 assistant 响应记录到 conversation_log"""
        log = ConversationLog(task_id="T-1", agent_name="test")

        # 不需要真正调 API — 直接测 log 接口
        log.add_system("system prompt")
        log.add_user("user message")
        log.add_assistant("response text", tool_calls=[
            {"id": "tc1", "name": "read_file", "input": {"path": "test.ts"}}
        ])
        log.add_token_usage(100, 200)

        d = log.to_dict()
        assert len(d["entries"]) == 2  # user + assistant
        assert d["tool_calls_count"] == 1
        assert d["token_usage"]["input_tokens"] == 100

    def test_tool_results_recorded(self) -> None:
        """工具结果被记录"""
        log = ConversationLog(task_id="T-1", agent_name="test")
        log.add_tool_result("tc1", "read_file", "file content")

        d = log.to_dict()
        assert len(d["entries"]) == 1
        entry = d["entries"][0]
        assert entry["content"]["tool_name"] == "read_file"
        assert entry["content"]["tool_use_id"] == "tc1"


# ── Orchestrator 集成 ───────────────────────────────────────────

class TestOrchestratorConversationLogging:
    """Orchestrator 对话日志集成"""

    def test_conversations_saved_on_task(self, tmp_path: Path) -> None:
        """任务执行时保存对话日志"""
        from agent_system.orchestrator import Orchestrator
        from agent_system.agents.coder import CodeChanges, FileChange
        from agent_system.agents.reflector import ReflectionReport
        from agent_system.models.context import AgentConfig, AgentContext
        from agent_system.models.project_config import ProjectConfig
        from agent_system.models.task import Task, TaskStatus, ReviewResult

        project = MagicMock(spec=ProjectConfig)
        project.coding_conventions = ""
        project.review_checklist = []
        project.review_commands = []
        project.project_root = str(tmp_path)
        context = AgentContext(project=project, config=AgentConfig())

        config = AgentConfig(dry_run=False)
        orch = Orchestrator(
            config=config,
            planner=MagicMock(),
            analyst=MagicMock(execute=MagicMock(return_value="analysis")),
            coder=MagicMock(execute=MagicMock(return_value=CodeChanges(files=[
                FileChange(path="test.ts", content="// test", action="create"),
            ]))),
            reviewer=MagicMock(execute=MagicMock(return_value=ReviewResult(passed=True))),
            reflector=MagicMock(execute=MagicMock(return_value=ReflectionReport.from_dict({
                "task_id": "T-1", "task_title": "Test",
            }))),
            context=context,
        )

        conv_dir = tmp_path / "agent-system" / "conversations"
        orch._conversation_logger = ConversationLogger(conv_dir)
        orch._reflections_dir = tmp_path / "agent-system" / "reflections"
        orch._reflections_dir.mkdir(parents=True, exist_ok=True)
        orch._state_store = MagicMock()
        orch._git = None
        orch._file_service = MagicMock()

        task = Task(
            id="T-1", title="Test", description="Test task",
            status=TaskStatus.PENDING,
        )
        orch.run_single_task(task)

        assert task.status == TaskStatus.DONE
        # 应有 analyst + coder + reviewer + reflector = 4 个对话日志
        files = list(conv_dir.rglob("*.json"))
        assert len(files) == 4

    def test_no_logger_still_works(self) -> None:
        """无 ConversationLogger 时不影响运行"""
        from agent_system.orchestrator import Orchestrator
        from agent_system.agents.coder import CodeChanges, FileChange as FC2
        from agent_system.models.context import AgentConfig, AgentContext
        from agent_system.models.project_config import ProjectConfig
        from agent_system.models.task import Task, TaskStatus, ReviewResult

        project = MagicMock(spec=ProjectConfig)
        project.coding_conventions = ""
        project.review_checklist = []
        project.review_commands = []
        project.project_root = "/test"
        context = AgentContext(project=project, config=AgentConfig())

        config = AgentConfig(dry_run=False)
        orch = Orchestrator(
            config=config,
            planner=MagicMock(),
            analyst=MagicMock(execute=MagicMock(return_value="ok")),
            coder=MagicMock(execute=MagicMock(return_value=CodeChanges(files=[
                FC2(path="test.ts", content="// test", action="create"),
            ]))),
            reviewer=MagicMock(execute=MagicMock(return_value=ReviewResult(passed=True))),
            reflector=None,
            context=context,
        )
        orch._conversation_logger = None
        orch._state_store = MagicMock()
        orch._git = None
        orch._file_service = MagicMock()

        task = Task(
            id="T-1", title="Test", description="Test task",
            status=TaskStatus.PENDING,
        )
        orch.run_single_task(task)
        assert task.status == TaskStatus.DONE
