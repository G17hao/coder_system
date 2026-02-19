"""Orchestrator — 主循环调度器"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from agent_system.agents.analyst import Analyst
from agent_system.agents.coder import Coder, CodeChanges
from agent_system.agents.planner import CyclicDependencyError, DependencyStatus, Planner
from agent_system.agents.reflector import Reflector, save_reflection
from agent_system.agents.reviewer import Reviewer
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import ReviewResult, Task, TaskStatus
from agent_system.services.conversation_logger import ConversationLogger
from agent_system.services.file_service import FileService
from agent_system.services.git_service import GitService, GitError
from agent_system.services.llm import LLMService
from agent_system.services.state_store import StateStore

logger = logging.getLogger(__name__)


class Orchestrator:
    """主循环调度器

    职责:
    - 加载项目配置，初始化上下文
    - 从任务队列取优先级最高的 pending 任务
    - 依次调用 Planner → Analyst → Coder → Reviewer
    - 根据结果更新任务状态、执行 git commit
    - 处理重试和失败
    """

    def __init__(
        self,
        config: AgentConfig,
        planner: Planner | None = None,
        analyst: Analyst | None = None,
        coder: Coder | None = None,
        reviewer: Reviewer | None = None,
        reflector: Reflector | None = None,
        context: AgentContext | None = None,
    ) -> None:
        self._config = config
        self._context = context

        # 如果提供了 agents 则使用（方便测试注入 mock）
        self._planner = planner
        self._analyst = analyst
        self._coder = coder
        self._reviewer = reviewer
        self._reflector = reflector

        self._llm: LLMService | None = None
        self._state_store: StateStore | None = None
        self._git: GitService | None = None
        self._file_service: FileService | None = None
        self._reflections_dir: Path | None = None
        self._conversation_logger: ConversationLogger | None = None

    @property
    def context(self) -> AgentContext:
        assert self._context is not None, "Orchestrator 未初始化"
        return self._context

    def initialize(self) -> None:
        """初始化: 加载项目配置 + 恢复/创建任务队列"""
        # 1. 加载项目配置
        if self._context is None:
            project = ProjectConfig.from_file(self._config.project_config_file)
            self._context = AgentContext(
                project=project,
                config=self._config,
            )

        project_root = self._context.project.project_root

        # 2. 初始化服务
        agent_system_dir = Path(project_root) / "agent-system"
        state_dir = agent_system_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_store = StateStore(state_dir / "tasks.json")
        self._file_service = FileService(project_root)

        # 反思目录
        self._reflections_dir = agent_system_dir / "reflections"
        self._reflections_dir.mkdir(parents=True, exist_ok=True)

        # 对话日志目录
        conversations_dir = agent_system_dir / "conversations"
        self._conversation_logger = ConversationLogger(conversations_dir)

        try:
            self._git = GitService(project_root)
        except GitError:
            logger.warning("Git 仓库未初始化，跳过 Git 操作")
            self._git = None

        # 3. 初始化 LLM 和 Agents
        if self._planner is None:
            llm = self._create_llm()
            self._llm = llm
            self._planner = Planner(llm=llm)
            self._analyst = Analyst(llm=llm)
            self._coder = Coder(llm=llm)
            self._reviewer = Reviewer(llm=llm)
            self._reflector = Reflector(llm=llm)

    def init_tasks(self) -> None:
        """从项目配置的 initial_tasks 创建初始任务队列"""
        assert self._context is not None
        tasks: list[Task] = []
        for seed in self._context.project.initial_tasks:
            tasks.append(Task(
                id=seed.id,
                title=seed.title,
                description=seed.description,
                dependencies=seed.dependencies,
                priority=seed.priority,
                phase=seed.phase,
                category=seed.category,
                created_by="initial",
            ))
        self._context.task_queue = tasks

        # 验证无循环依赖
        assert self._planner is not None
        self._planner.validate_no_cycles(tasks)

        # 持久化
        self._save_state()

        logger.info(f"初始化 {len(tasks)} 个任务")

    def resume_tasks(self) -> None:
        """从持久化状态恢复任务队列"""
        assert self._state_store is not None
        assert self._context is not None

        if self._state_store.exists():
            tasks = self._state_store.load()
            self._context.task_queue = tasks
            # 重建 completed_tasks
            self._context.completed_tasks = {
                t.id: t for t in tasks if t.status == TaskStatus.DONE
            }
            logger.info(
                f"恢复 {len(tasks)} 个任务，"
                f"已完成 {len(self._context.completed_tasks)} 个"
            )
        else:
            logger.info("无状态文件，从初始任务开始")
            self.init_tasks()

    def run(self) -> None:
        """主循环: 持续执行直到所有任务完成或失败"""
        assert self._context is not None
        assert self._planner is not None

        iteration = 0
        max_idle = 50  # 防止无限循环

        while iteration < max_idle:
            iteration += 1

            # 同步 LLM 使用量到 context
            self._sync_llm_usage()

            # 检查 token 预算
            if self._config.budget_limit > 0 and self._context.total_tokens_used >= self._config.budget_limit:
                logger.warning(
                    f"Token 预算耗尽 ({self._context.total_tokens_used}/{self._config.budget_limit})，暂停"
                )
                self._save_state()
                print("Token budget exceeded, pausing...")
                break

            # 检查 API 调用次数限制
            if self._config.call_limit > 0 and self._context.total_api_calls >= self._config.call_limit:
                logger.warning(
                    f"API 调用次数耗尽 ({self._context.total_api_calls}/{self._config.call_limit})，暂停"
                )
                self._save_state()
                print("API call limit exceeded, pausing...")
                break

            # 获取下一个可执行任务
            task = self._planner.get_next_pending(self._context)

            if task is None:
                # 检查是否还有 blocked 任务
                blocked = [
                    t for t in self._context.task_queue
                    if t.status in (TaskStatus.PENDING, TaskStatus.BLOCKED)
                ]
                if not blocked:
                    logger.info("所有任务已完成或失败")
                    break

                # 尝试解锁 blocked 任务
                unlocked = self._try_unlock_blocked()
                if not unlocked:
                    logger.warning("存在 blocked 任务但无法解锁，退出")
                    break
                continue

            # 执行单个任务
            self.run_single_task(task)
            self._save_state()

        # 输出最终报告
        self._print_report()

    def run_single_task(self, task: Task) -> None:
        """执行单个任务的完整流程: Analyst → Coder → Reviewer

        Args:
            task: 要执行的任务
        """
        assert self._analyst is not None
        assert self._coder is not None
        assert self._reviewer is not None
        assert self._context is not None

        task.status = TaskStatus.IN_PROGRESS
        self._context.current_task = task
        logger.info(f">> 开始任务 {task.id}: {task.title}")

        try:
            # 4. 分析阶段
            if task.analysis_cache is None:
                logger.info(f"  [分析] 分析中...")
                if not self._config.dry_run:
                    conv_log = self._start_conversation(task, "analyst")
                    report = self._analyst.execute(
                        task, self._context, conversation_log=conv_log,
                    )
                    self._save_conversation()
                    task.analysis_cache = report
                    self._sync_llm_usage()
                else:
                    task.analysis_cache = '{"dry_run": true}'

            # 5~7. 编码→审查循环（含重试）
            while task.retry_count < task.max_retries:
                # 5. 编码阶段
                logger.info(f"  [编码] 编码中... (尝试 {task.retry_count + 1}/{task.max_retries})")
                if not self._config.dry_run:
                    conv_log = self._start_conversation(task, "coder")
                    changes = self._coder.execute(
                        task,
                        self._context,
                        analysis_report=task.analysis_cache or "",
                        conversation_log=conv_log,
                    )
                    self._save_conversation()
                    task.coder_output = str(changes.to_dict())
                    self._sync_llm_usage()
                else:
                    changes = CodeChanges(files=[])
                    task.coder_output = "{dry_run: true}"

                # 6. 写入文件
                if not self._config.dry_run:
                    self._write_changes(changes)

                # 7. 审查阶段
                logger.info(f"  [审查] 审查中...")
                if not self._config.dry_run:
                    conv_log = self._start_conversation(task, "reviewer")
                    result = self._reviewer.execute(
                        task, self._context, code_changes=changes,
                        conversation_log=conv_log,
                    )
                    self._save_conversation()
                    self._sync_llm_usage()
                else:
                    result = ReviewResult(passed=True)

                task.review_result = result

                if result.passed:
                    # 成功: git commit
                    commit_hash = self._git_commit(task)
                    task.status = TaskStatus.DONE
                    task.commit_hash = commit_hash
                    self._context.completed_tasks[task.id] = task
                    logger.info(f"  [done] 任务 {task.id} 完成 (commit: {commit_hash or 'N/A'})")
                    self._run_reflection(task)
                    return
                else:
                    # 失败: 撤销文件变更 + 重试
                    logger.warning(
                        f"  [fail] 审查未通过: {result.issues}"
                    )
                    if not self._config.dry_run:
                        self._revert_changes()
                    task.retry_count += 1

            # 超过最大重试次数
            task.status = TaskStatus.FAILED
            task.error = "\n".join(
                task.review_result.issues if task.review_result else ["超过最大重试次数"]
            )
            logger.error(f"  [failed] 任务 {task.id} 失败: {task.error}")
            self._run_reflection(task)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"  [error] 任务 {task.id} 异常: {e}")
            self._sync_llm_usage()
            self._run_reflection(task)

        finally:
            self._context.current_task = None

    def run_until(self, task_count: int) -> None:
        """执行指定数量的任务后停止（用于测试）

        Args:
            task_count: 要执行的任务数量
        """
        assert self._planner is not None
        assert self._context is not None

        executed = 0
        while executed < task_count:
            task = self._planner.get_next_pending(self._context)
            if task is None:
                break
            self.run_single_task(task)
            executed += 1
        self._save_state()

    def next_pending_task(self) -> Task | None:
        """获取下一个待执行任务（用于测试断点恢复）"""
        assert self._planner is not None
        return self._planner.get_next_pending(self._context)

    def get_status_report(self) -> str:
        """生成任务状态报告"""
        assert self._context is not None

        counts: dict[str, int] = {}
        for task in self._context.task_queue:
            status = task.status.value
            counts[status] = counts.get(status, 0) + 1

        lines = [f"任务状态报告 ({len(self._context.task_queue)} 个任务):"]
        for status, count in sorted(counts.items()):
            lines.append(f"  {status}: {count}")

        return "\n".join(lines)

    def reset_failed_tasks(self) -> None:
        """将所有 failed 任务重置为 pending，清除错误信息，允许重新执行"""
        assert self._context is not None
        count = 0
        for task in self._context.task_queue:
            if task.status == TaskStatus.FAILED:
                task.status = TaskStatus.PENDING
                task.error = None
                task.retry_count = 0
                count += 1
        if count:
            logger.info(f"已重置 {count} 个失败任务为 pending")
            self._save_state()

    @classmethod
    def from_state(cls, config: AgentConfig) -> Orchestrator:
        """从持久化状态创建 Orchestrator（断点恢复）"""
        orch = cls(config=config)
        orch.initialize()
        orch.resume_tasks()
        return orch

    # --- 内部方法 ---

    def _sync_llm_usage(self) -> None:
        """将 LLMService 的累计 usage 同步到 AgentContext"""
        if self._llm is None or self._context is None:
            return
        usage = self._llm.usage
        self._context.total_tokens_used = usage.total
        self._context.total_api_calls = usage.total_calls

    def _create_llm(self) -> LLMService:
        """创建 LLM 服务实例"""
        api_key = self._config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        base_url = self._config.anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        return LLMService(
            api_key=api_key,
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            base_url=base_url,
            timeout=self._config.llm_timeout,
            max_retries=self._config.llm_max_retries,
        )

    def _save_state(self) -> None:
        """持久化当前任务队列状态"""
        if self._state_store and self._context:
            self._state_store.save(self._context.task_queue)

    def _write_changes(self, changes: CodeChanges) -> None:
        """将代码变更写入磁盘"""
        if not self._file_service:
            return
        for f in changes.files:
            self._file_service.write(f.path, f.content)
            logger.info(f"    [write] 写入: {f.path}")

    def _git_commit(self, task: Task) -> str | None:
        """Git 提交"""
        if not self._git or not self._config.git_auto_commit or self._config.dry_run:
            return None
        try:
            if self._git.has_changes():
                self._git.add_all()
                return self._git.commit(f"agent: {task.id} - {task.title}")
        except GitError as e:
            logger.warning(f"Git commit 失败: {e}")
        return None

    def _revert_changes(self) -> None:
        """撤销文件变更"""
        if not self._git:
            return
        try:
            self._git.checkout_files()
        except GitError as e:
            logger.warning(f"Git revert 失败: {e}")

    def _run_reflection(self, task: Task) -> None:
        """执行反思并保存报告

        Args:
            task: 刚完成或失败的任务
        """
        if self._config.dry_run or self._reflector is None or self._context is None:
            return

        try:
            logger.info(f"  [反思] 反思中...")
            conv_log = self._start_conversation(task, "reflector")
            report = self._reflector.execute(
                task, self._context, conversation_log=conv_log,
            )
            self._save_conversation()
            self._sync_llm_usage()
            if self._reflections_dir:
                save_reflection(report, self._reflections_dir)
        except Exception as e:
            # 反思失败不应影响主流程
            logger.warning(f"  [反思] 反思失败 (不影响任务结果): {e}")

    def _start_conversation(self, task: Task, agent_name: str) -> Any:
        """开始一个新的对话记录

        Args:
            task: 当前任务
            agent_name: Agent 名称

        Returns:
            ConversationLog 实例，或无日志器时返回 None
        """
        if self._conversation_logger is None:
            return None
        return self._conversation_logger.start(task.id, agent_name)

    def _save_conversation(self) -> None:
        """保存当前对话日志"""
        if self._conversation_logger is None:
            return
        filepath = self._conversation_logger.finish_and_save()
        if filepath:
            logger.info(f"    [对话] 已保存: {filepath}")

    def _try_unlock_blocked(self) -> bool:
        """尝试解锁 blocked 任务"""
        assert self._planner is not None
        assert self._context is not None

        unlocked = False
        for task in self._context.task_queue:
            if task.status != TaskStatus.BLOCKED:
                continue
            status = self._planner.check_dependencies(
                task, self._context.completed_tasks, context=self._context,
            )
            if status == DependencyStatus.READY:
                task.status = TaskStatus.PENDING
                unlocked = True
            elif status == DependencyStatus.MISSING:
                # 尝试动态生成缺失依赖
                missing_ids = [
                    dep for dep in task.dependencies
                    if dep not in self._context.completed_tasks
                    and dep not in {t.id for t in self._context.task_queue}
                ]
                if missing_ids and not self._config.dry_run:
                    new_tasks = self._planner.generate_missing(missing_ids, self._context)
                    self._context.task_queue.extend(new_tasks)
                    if new_tasks:
                        unlocked = True

        return unlocked

    def _print_report(self) -> None:
        """输出执行报告"""
        assert self._context is not None

        done = sum(1 for t in self._context.task_queue if t.status == TaskStatus.DONE)
        failed = sum(1 for t in self._context.task_queue if t.status == TaskStatus.FAILED)
        pending = sum(1 for t in self._context.task_queue if t.status == TaskStatus.PENDING)
        blocked = sum(1 for t in self._context.task_queue if t.status == TaskStatus.BLOCKED)
        total = len(self._context.task_queue)

        # 同步最终 usage
        self._sync_llm_usage()

        report = (
            f"\n{'='*50}\n"
            f"执行报告\n"
            f"{'='*50}\n"
            f"总任务数: {total}\n"
            f"  [done] 完成: {done}\n"
            f"  [fail] 失败: {failed}\n"
            f"  [wait] 等待: {pending}\n"
            f"  [block] 阻塞: {blocked}\n"
            f"Token 使用: {self._context.total_tokens_used}\n"
            f"API 调用: {self._context.total_api_calls}\n"
            f"{'='*50}"
        )
        try:
            print(report)
        except UnicodeEncodeError:
            print(report.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
        logger.info(report)
