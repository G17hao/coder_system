"""Step 2 测试：数据模型 + 服务层"""

import json
import tempfile
from pathlib import Path

import pytest

from agent_system.models.task import Task, TaskStatus, ReviewResult
from agent_system.models.project_config import ProjectConfig
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.services.state_store import StateStore
from agent_system.services.git_service import GitService
from agent_system.services.file_service import FileService

FIXTURES = Path(__file__).parent / "fixtures"


class TestTaskSerialization:
    """Task 序列化/反序列化测试"""

    def test_round_trip(self) -> None:
        """序列化→反序列化往返一致"""
        task = Task(
            id="T0.1",
            title="test task",
            description="test description",
            status=TaskStatus.PENDING,
            dependencies=["T0.0"],
            priority=5,
            phase=1,
            category="infrastructure",
        )
        json_str = task.to_json()
        restored = Task.from_json(json_str)
        assert task.id == restored.id
        assert task.title == restored.title
        assert task.description == restored.description
        assert task.status == restored.status
        assert task.dependencies == restored.dependencies
        assert task.priority == restored.priority
        assert task.phase == restored.phase
        assert task.category == restored.category

    def test_round_trip_with_review(self) -> None:
        """带 ReviewResult 的序列化往返"""
        task = Task(
            id="T1.1",
            title="reviewed task",
            description="desc",
            status=TaskStatus.DONE,
            review_result=ReviewResult(
                passed=True,
                issues=[],
                suggestions=["good job"],
            ),
            commit_hash="abc123",
        )
        json_str = task.to_json()
        restored = Task.from_json(json_str)
        assert restored.review_result is not None
        assert restored.review_result.passed is True
        assert restored.review_result.suggestions == ["good job"]
        assert restored.commit_hash == "abc123"

    def test_status_enum(self) -> None:
        """TaskStatus 枚举值正确"""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.IN_PROGRESS.value == "in-progress"
        assert TaskStatus.BLOCKED.value == "blocked"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.SKIPPED.value == "skipped"


class TestStateStore:
    """StateStore 状态持久化测试"""

    def test_save_and_load(self) -> None:
        """写入→读取→恢复完整任务队列"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state" / "tasks.json")
            task1 = Task(id="T0.1", title="task1", description="desc1")
            task2 = Task(
                id="T1.1",
                title="task2",
                description="desc2",
                dependencies=["T0.1"],
                status=TaskStatus.DONE,
            )
            store.save([task1, task2])
            loaded = store.load()
            assert len(loaded) == 2
            assert loaded[0].id == "T0.1"
            assert loaded[1].id == "T1.1"
            assert loaded[1].status == TaskStatus.DONE

    def test_load_empty(self) -> None:
        """加载不存在的文件返回空列表"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "nonexistent.json")
            assert store.load() == []

    def test_exists(self) -> None:
        """exists 方法正确报告文件状态"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "tasks.json")
            assert store.exists() is False
            store.save([])
            assert store.exists() is True


class TestProjectConfig:
    """ProjectConfig 加载校验测试"""

    def test_load_valid(self) -> None:
        """正常 JSON 加载成功"""
        config = ProjectConfig.from_file(FIXTURES / "valid_project.json")
        assert config.project_name == "test-project"
        assert config.project_description == "测试项目描述"
        assert len(config.initial_tasks) == 2
        assert config.initial_tasks[0].id == "T0.1"
        assert len(config.pattern_mappings) == 1
        assert config.review_checklist == ["无 any 类型", "编译通过"]

    def test_load_invalid_missing_fields(self) -> None:
        """缺少必填字段 → 抛 ValueError"""
        with pytest.raises(ValueError, match="缺少必填字段"):
            ProjectConfig.from_file(FIXTURES / "invalid_project.json")

    def test_load_nonexistent(self) -> None:
        """不存在的文件 → 抛 FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            ProjectConfig.from_file("/tmp/nonexistent_project.json")


class TestGitService:
    """GitService 基本测试"""

    def test_current_branch(self) -> None:
        """current_branch 返回非空字符串"""
        # 使用当前项目 repo（假设在 git 仓库中）
        git = GitService(Path(__file__).parent.parent)
        branch = git.current_branch()
        assert len(branch) > 0
        assert isinstance(branch, str)


class TestFileService:
    """FileService 文件操作测试"""

    def test_delete_file(self) -> None:
        """删除存在文件返回 True，重复删除返回 False"""
        with tempfile.TemporaryDirectory() as tmp:
            svc = FileService(tmp)
            rel = "a/test.txt"
            svc.write(rel, "hello")
            assert svc.exists(rel) is True

            deleted = svc.delete(rel)
            assert deleted is True
            assert svc.exists(rel) is False

            deleted_again = svc.delete(rel)
            assert deleted_again is False


class TestAgentContext:
    """AgentContext 基本测试"""

    def test_create_context(self) -> None:
        """能正确创建 AgentContext"""
        config = ProjectConfig(
            project_name="test",
            project_description="desc",
            project_root="/tmp",
        )
        ctx = AgentContext(project=config)
        assert ctx.project.project_name == "test"
        assert ctx.task_queue == []
        assert ctx.completed_tasks == {}
        assert ctx.current_task is None
        assert ctx.total_tokens_used == 0
        assert ctx.total_api_calls == 0
