"""Orchestrator — 主循环调度器"""

from __future__ import annotations

import logging
import os
import json
import re
from pathlib import Path
from typing import Any

from agent_system.agents.analyst import Analyst
from agent_system.agents.coder import Coder, CodeChanges
from agent_system.agents.planner import CyclicDependencyError, DependencyStatus, Planner
from agent_system.agents.reflector import Reflector, save_reflection
from agent_system.agents.reviewer import Reviewer
from agent_system.agents.supervisor import Supervisor, SupervisorDecision
from agent_system.models.context import AgentConfig, AgentContext
from agent_system.models.project_config import ProjectConfig
from agent_system.models.task import ReviewResult, Task, TaskStatus
from agent_system.services.conversation_logger import ConversationLogger
from agent_system.services.file_service import FileService
from agent_system.services.git_service import GitService, GitError
from agent_system.services.llm import LLMService
from agent_system.services.state_store import StateStore

logger = logging.getLogger(__name__)

_RETRY_FUSE_THRESHOLD = 3


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
        supervisor: Supervisor | None = None,
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
        self._supervisor = supervisor

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
            # 自动切换到项目配置指定的分支
            if hasattr(self._context.project, 'git_branch') and self._context.project.git_branch:
                target_branch = self._context.project.git_branch
                current_branch = self._git.current_branch()
                if current_branch != target_branch:
                    logger.info(f"切换到分支: {target_branch}")
                    self._git.create_branch(target_branch)
        except GitError as e:
            logger.warning(f"Git 初始化失败: {e}，跳过 Git 操作")
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
            self._supervisor = Supervisor(llm=llm)

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

            # 任务失败/阻塞后暂停，等待用户输入提示词
            if task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
                should_continue = self._prompt_user_hint(task)
                if not should_continue:
                    logger.info("用户选择停止，退出主循环")
                    break
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

            # 4.1 分析阶段可拆解子任务：写入队列并优先执行
            generated_subtasks = self._create_subtasks_from_analysis(task)
            if generated_subtasks > 0:
                task.status = TaskStatus.PENDING
                logger.info(
                    f"  [分析] 已拆解 {generated_subtasks} 个子任务，父任务回到 pending 等待子任务完成"
                )
                return

            # 5~7. 编码→审查循环（含重试 + Supervisor 介入）
            supervised = False  # Supervisor 每次任务执行中至多介入一次
            while True:
                # 内层：Coder → Reviewer 重试循环
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

                    # 5.1 编码空输出检测 → 跳过审查，直接重试
                    if not self._config.dry_run and not changes.files:
                        logger.warning(
                            f"  [编码] Coder 输出空文件列表，跳过审查直接重试"
                        )
                        task.retry_count += 1
                        if (
                            task.retry_count > _RETRY_FUSE_THRESHOLD
                            and not supervised
                            and not self._config.dry_run
                            and self._supervisor is not None
                        ):
                            logger.warning(
                                f"  [fuse] 任务 {task.id} 已重试 {task.retry_count} 次，触发 Supervisor 根因分析"
                            )
                            break
                        continue

                    # 6. 写入文件（Coder 工具循环已直接写入磁盘，这里仅做兜底）
                    if not self._config.dry_run:
                        self._write_changes(changes)

                    # 6.1 Supervisor 必改文件对账（硬门禁，先于 Reviewer）
                    reconcile_issues, reconcile_suggestions = self._validate_must_change_files(task, changes)
                    if reconcile_issues:
                        task.review_result = ReviewResult(
                            passed=False,
                            issues=reconcile_issues,
                            suggestions=reconcile_suggestions,
                        )
                        logger.warning(
                            f"  [对账] 必改文件未覆盖，跳过审查直接重试 ({len(reconcile_issues)} 个问题)"
                        )
                        task.retry_count += 1
                        if (
                            task.retry_count > _RETRY_FUSE_THRESHOLD
                            and not supervised
                            and not self._config.dry_run
                            and self._supervisor is not None
                        ):
                            logger.warning(
                                f"  [fuse] 任务 {task.id} 已重试 {task.retry_count} 次，触发 Supervisor 根因分析"
                            )
                            break
                        continue

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

                    # 7.1 覆盖性 + 一致性校验（降级为建议，不再直接 fail）
                    alignment_issues, alignment_suggestions = self._validate_alignment(task, changes)
                    if alignment_issues:
                        result.suggestions.extend(alignment_issues)
                        result.suggestions.extend(alignment_suggestions)
                        logger.info(
                            f"  [审查] 对齐校验产出建议 {len(alignment_issues)} 条（不阻断通过）"
                        )

                    task.review_result = result

                    if result.passed:
                        # 成功: LLM 生成 commit message 并提交
                        commit_hash = self._git_commit(task, changes)
                        task.status = TaskStatus.DONE
                        task.commit_hash = commit_hash
                        self._context.completed_tasks[task.id] = task
                        logger.info(f"  [done] 任务 {task.id} 完成 (commit: {commit_hash or 'N/A'})")
                        self._run_reflection(task)
                        return
                    else:
                        # 失败: 保留文件，让 Coder 在下次重试时修复
                        logger.warning(
                            f"  [fail] 审查未通过 ({len(result.issues)} 个问题)，保留文件供下轮修复"
                        )
                        task.retry_count += 1
                        if (
                            task.retry_count > _RETRY_FUSE_THRESHOLD
                            and not supervised
                            and not self._config.dry_run
                            and self._supervisor is not None
                        ):
                            logger.warning(
                                f"  [fuse] 任务 {task.id} 已重试 {task.retry_count} 次，触发 Supervisor 根因分析"
                            )
                            break

                # 内层重试耗尽 —— 判断是否需要 Supervisor
                if supervised or self._config.dry_run or self._supervisor is None:
                    break  # 不再介入，直接走 FAILED 流程

                # Supervisor 介入
                logger.info(f"  [supervisor] 重试耗尽，Supervisor 介入判断...")
                supervised = True
                if not self._config.dry_run:
                    conv_log = self._start_conversation(task, "supervisor")
                    decision: SupervisorDecision = self._supervisor.execute(
                        task, self._context, conversation_log=conv_log,
                    )
                    self._save_conversation()
                    self._sync_llm_usage()
                else:
                    decision = SupervisorDecision(action="halt", reason="dry_run")

                if decision.action == "halt":
                    task.status = TaskStatus.BLOCKED
                    task.error = f"[Supervisor] {decision.reason}"
                    logger.warning(
                        f"  [supervisor] 任务 {task.id} 暂停，等待人工介入\n"
                        f"  原因: {decision.reason}"
                    )
                    self._run_reflection(task)
                    return
                else:  # continue
                    task.max_retries += decision.extra_retries
                    task.supervisor_hint = decision.hint
                    task.supervisor_plan = self._build_supervisor_plan_text(decision)
                    task.supervisor_must_change_files = [
                        self._normalize_file_path(path)
                        for path in decision.must_change_files
                        if self._normalize_file_path(path)
                    ]
                    logger.info(
                        f"  [supervisor] 追加 {decision.extra_retries} 次重试，"
                        f"修复提示: {decision.hint[:120]}"
                    )
                    if task.supervisor_plan:
                        logger.info(
                            f"  [supervisor] 已生成重规划（{len(task.supervisor_plan)} 字符），"
                            f"下一轮将强制注入 Coder"
                        )
                    if task.supervisor_must_change_files:
                        logger.info(
                            f"  [supervisor] 已登记必须覆盖文件 {len(task.supervisor_must_change_files)} 个"
                        )
                    # 重新进入外层循环 → 内层 while 继续执行

            # 重试耗尽（含 Supervisor 追加机会）仍失败
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
            if not self._config.dry_run:
                self._revert_changes()
            self._sync_llm_usage()
            self._run_reflection(task)

        finally:
            self._context.current_task = None
            # 无论通过 run() 还是直接 run_single_task() 调用，
            # 任务收敛后都立即落盘，确保下次可从断点恢复。
            self._save_state()

    def _validate_alignment(self, task: Task, changes: CodeChanges | None) -> tuple[list[str], list[str]]:
        """审查补充校验：覆盖性 + 一致性

        Returns:
            (issues, suggestions)
        """
        if changes is None:
            return (["[一致性校验] 缺少编码产物，无法验证任务完成度"], ["请确保 Coder 产出有效文件变更"])

        changed_files = {
            self._normalize_file_path(f.path)
            for f in changes.files
            if getattr(f, "path", None)
        }

        analysis_data = self._parse_analysis_json(task.analysis_cache or "")
        if not analysis_data:
            return ([], [])

        issues: list[str] = []
        suggestions: list[str] = []

        # 覆盖性校验：分析阶段识别的关键文件是否被覆盖
        key_files = self._extract_key_files(analysis_data)
        if key_files:
            missing = [
                f for f in key_files
                if f not in changed_files and not self._workspace_file_exists(f)
            ]
            if missing:
                issues.append(
                    "[覆盖性校验] 分析识别的关键文件未被覆盖且工作区不存在: "
                    + ", ".join(missing[:8])
                )
                suggestions.append("请优先修改或创建分析阶段标记的关键文件，并在输出中说明修复对应关系")

        # 一致性校验：任务描述 / 分析缺口 / 改动文件三方对齐
        # 规则：若分析缺口中出现明确文件路径，这些路径应在改动列表中出现
        gap_files = self._extract_gap_file_refs(analysis_data)
        if gap_files:
            unresolved_gap_files = [
                f for f in gap_files
                if f not in changed_files and not self._workspace_file_exists(f)
            ]
            if unresolved_gap_files:
                issues.append(
                    "[一致性校验] 分析缺口指向的文件未被修改且工作区不存在: "
                    + ", ".join(unresolved_gap_files[:8])
                )
                suggestions.append("请逐条对齐分析 gaps，确保相关文件已创建或在本次改动中覆盖")

        return (issues, suggestions)

    def _create_subtasks_from_analysis(self, task: Task) -> int:
        """从分析报告中提取子任务并写入队列。"""
        if task.analysis_subtasks_generated:
            return 0

        analysis_data = self._parse_analysis_json(task.analysis_cache or "")
        if not analysis_data:
            task.analysis_subtasks_generated = True
            return 0

        raw_subtasks = analysis_data.get("subtasks", [])
        if not isinstance(raw_subtasks, list):
            task.analysis_subtasks_generated = True
            return 0

        if not self._context:
            task.analysis_subtasks_generated = True
            return 0

        existing_ids = {t.id for t in self._context.task_queue}
        created: list[Task] = []
        generated_ids: list[str] = []

        for index, item in enumerate(raw_subtasks, start=1):
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            if not title or not description:
                continue

            requested_id = str(item.get("id", "")).strip()
            base_id = requested_id or f"{task.id}.S{index}"
            subtask_id = base_id
            suffix = 1
            while subtask_id in existing_ids:
                suffix += 1
                subtask_id = f"{base_id}_{suffix}"

            item_priority_raw = item.get("priority")
            item_priority = task.priority - 1
            if isinstance(item_priority_raw, int):
                item_priority = item_priority_raw
            item_priority = max(0, item_priority)

            inherited_deps = list(task.dependencies)
            item_dependencies_raw = item.get("dependencies", [])
            item_dependencies = [
                str(dep).strip() for dep in item_dependencies_raw
                if str(dep).strip()
            ] if isinstance(item_dependencies_raw, list) else []
            merged_dependencies = [
                dep for dep in (inherited_deps + item_dependencies)
                if dep and dep != subtask_id
            ]
            merged_dependencies = list(dict.fromkeys(merged_dependencies))

            subtask = Task(
                id=subtask_id,
                title=title,
                description=description,
                dependencies=merged_dependencies,
                priority=item_priority,
                phase=task.phase,
                category=str(item.get("category", "")).strip() or task.category,
                created_by="planner",
            )
            created.append(subtask)
            generated_ids.append(subtask_id)
            existing_ids.add(subtask_id)

        task.analysis_subtasks_generated = True
        if not created:
            return 0

        self._context.task_queue.extend(created)

        current_dependencies = [dep for dep in task.dependencies if dep]
        task.dependencies = list(dict.fromkeys(current_dependencies + generated_ids))

        if self._planner is not None:
            self._planner.validate_no_cycles(self._context.task_queue)

        return len(created)

    def _validate_must_change_files(self, task: Task, changes: CodeChanges | None) -> tuple[list[str], list[str]]:
        """对账硬门禁：Supervisor 指定的 must_change_files 必须在本轮改动中出现。"""
        required_files = [
            self._normalize_file_path(path)
            for path in task.supervisor_must_change_files
            if self._normalize_file_path(path)
        ]
        if not required_files:
            return ([], [])

        if changes is None:
            return (
                ["[对账] 缺少编码产物，无法核对 must_change_files"],
                ["请按 Supervisor 计划优先覆盖 must_change_files"],
            )

        changed_files = {
            self._normalize_file_path(f.path)
            for f in changes.files
            if getattr(f, "path", None)
        }

        missing = [path for path in required_files if path not in changed_files]
        if not missing:
            return ([], [])

        return (
            [
                "[对账] 以下 must_change_files 未在本轮改动中覆盖: "
                + ", ".join(missing[:8])
            ],
            ["请逐项对账 Supervisor 指定文件，并在 coder_output 标注文件与问题映射"],
        )

    def _parse_analysis_json(self, analysis_text: str) -> dict[str, Any] | None:
        """从 Analyst 文本中提取 JSON 结构。"""
        if not analysis_text:
            return None

        code_block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", analysis_text)
        candidates: list[str] = []
        if code_block_match:
            candidates.append(code_block_match.group(1))

        start = analysis_text.find("{")
        end = analysis_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(analysis_text[start:end + 1])

        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
        return None

    def _extract_key_files(self, analysis_data: dict[str, Any]) -> list[str]:
        files = analysis_data.get("files", [])
        if not isinstance(files, list):
            return []

        result: list[str] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            action = str(item.get("action", "")).lower()
            if isinstance(path, str) and path.strip() and action in ("create", "modify", "update", "delete"):
                normalized = self._normalize_file_path(path)
                if normalized:
                    result.append(normalized)
        return sorted(set(result))

    def _extract_gap_file_refs(self, analysis_data: dict[str, Any]) -> list[str]:
        gaps = analysis_data.get("gaps", [])
        if not isinstance(gaps, list):
            return []

        refs: set[str] = set()
        pattern = re.compile(r"([\w./\\-]+\.(?:ts|tsx|js|jsx|py|json|md))", re.IGNORECASE)
        for gap in gaps:
            if not isinstance(gap, str):
                continue
            for m in pattern.findall(gap):
                normalized = self._normalize_file_path(m)
                if normalized:
                    refs.add(normalized)
        return sorted(refs)

    def _normalize_file_path(self, path: str) -> str:
        cleaned = path.replace("\\", "/").strip()
        if not cleaned:
            return ""

        project_root = self._context.project.project_root.replace("\\", "/") if self._context else ""
        if project_root and cleaned.startswith(project_root):
            cleaned = cleaned[len(project_root):].lstrip("/")

        # 兼容绝对路径和盘符路径
        if ":/" in cleaned:
            cleaned = cleaned.split(":/", 1)[1]
        cleaned = cleaned.lstrip("/")

        return cleaned

    def _workspace_file_exists(self, normalized_rel_path: str) -> bool:
        """检查工作区内相对路径文件是否存在。"""
        if not self._context or not normalized_rel_path:
            return False
        base = Path(self._context.project.project_root)
        target = (base / normalized_rel_path).resolve()
        return target.exists() and target.is_file()

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
        """将所有 failed/blocked/in-progress 任务重置为 pending，清除错误信息，允许重新执行"""
        assert self._context is not None
        count = 0
        for task in self._context.task_queue:
            if task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS):
                task.status = TaskStatus.PENDING
                task.error = None
                task.retry_count = 0
                task.supervisor_plan = None
                count += 1
        if count:
            logger.info(f"已重置 {count} 个失败/阻塞任务为 pending")
            self._save_state()

    @classmethod
    def from_state(cls, config: AgentConfig) -> Orchestrator:
        """从持久化状态创建 Orchestrator（断点恢复）"""
        orch = cls(config=config)
        orch.initialize()
        orch.resume_tasks()
        return orch

    # --- 内部方法 ---

    def _prompt_user_hint(self, task: Task) -> bool:
        """任务失败/阻塞后，交互式询问用户是否继续及提示词。

        Returns:
            True  — 用户输入了提示词，任务已重置为 PENDING，继续主循环
            False — 用户直接回车（无输入），停止主循环
        """
        import sys

        print()
        print("=" * 60)
        print(f"[暂停] 任务 {task.id} ({task.status.value}): {task.title}")
        if task.error:
            print(f"  错误: {task.error[:300]}")
        print()
        print("请输入修复提示词让 Coder 重新尝试，或直接回车停止运行:")
        print("  (输入提示后按回车继续；直接回车退出)")
        print("=" * 60)

        # 非交互式环境直接停止
        if not sys.stdin.isatty():
            print("[非交互式环境] 自动停止")
            return False

        try:
            hint = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        if not hint:
            return False

        # 重置任务状态，附加用户提示词
        task.supervisor_hint = hint
        task.supervisor_plan = None
        task.status = TaskStatus.PENDING
        task.retry_count = 0
        task.error = None
        logger.info(
            f"  [用户提示] 任务 {task.id} 重置为 PENDING，提示: {hint[:120]}"
        )
        return True

    def _sync_llm_usage(self) -> None:
        """将 LLMService 的累计 usage 同步到 AgentContext"""
        if self._llm is None or self._context is None:
            return
        usage = self._llm.usage
        self._context.total_tokens_used = usage.total
        self._context.total_api_calls = usage.total_calls

    @staticmethod
    def _build_supervisor_plan_text(decision: SupervisorDecision) -> str:
        """将 Supervisor 结构化决策渲染为可直接注入 Coder 的计划文本"""
        if decision.action != "continue":
            return ""

        lines: list[str] = []
        if decision.plan_summary:
            lines.append(f"计划摘要: {decision.plan_summary}")
        if decision.must_change_files:
            lines.append("必须修改文件:")
            lines.extend(f"- {path}" for path in decision.must_change_files)
        if decision.execution_checklist:
            lines.append("执行清单:")
            lines.extend(f"- {item}" for item in decision.execution_checklist)
        if decision.validation_steps:
            lines.append("验证步骤:")
            lines.extend(f"- {step}" for step in decision.validation_steps)
        if decision.unknowns:
            lines.append("未知信息(需补充):")
            lines.extend(f"- {item}" for item in decision.unknowns)

        return "\n".join(lines).strip()

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
            if f.action == "delete":
                deleted = self._file_service.delete(f.path)
                if deleted:
                    logger.info(f"    [write] 删除: {f.path}")
                else:
                    logger.info(f"    [write] 删除跳过: {f.path}（文件不存在）")
                continue
            if f.content is None:
                logger.info(f"    [write] 跳过: {f.path}（无内联内容，默认已由工具写入）")
                continue
            self._file_service.write(f.path, f.content)
            logger.info(f"    [write] 写入: {f.path}")

    def _git_commit(self, task: Task, changes: CodeChanges | None = None) -> str | None:
        """让 LLM 生成 commit message 并提交

        Args:
            task: 当前完成的任务
            changes: 代码变更（用于生成 commit message）

        Returns:
            commit hash 或 None
        """
        if not self._git:
            logger.warning("  [git] 跳过提交: GitService 未初始化")
            return None
        if not self._config.git_auto_commit:
            logger.info("  [git] 跳过提交: git_auto_commit=False")
            return None
        if self._config.dry_run:
            logger.info("  [git] 跳过提交: dry_run 模式")
            return None
        try:
            if not self._git.has_changes():
                logger.info("  [git] 跳过提交: 无文件变更")
                return None

            # 让 LLM 生成 commit message
            commit_msg = self._generate_commit_message(task, changes)
            self._git.add_all()
            commit_hash = self._git.commit(commit_msg)
            logger.info(f"  [git] 提交成功: {commit_hash[:8]} {commit_msg.splitlines()[0]}")
            return commit_hash
        except GitError as e:
            logger.warning(f"  [git] 提交失败: {e}")
        return None

    def _generate_commit_message(self, task: Task, changes: CodeChanges | None) -> str:
        """让 LLM 根据任务和变更内容生成 git commit message

        Args:
            task: 当前任务
            changes: 代码变更

        Returns:
            commit message 字符串
        """
        if not self._llm or not changes or not changes.files:
            return f"feat({task.id}): {task.title}"

        # 构建变更摘要
        file_summary = []
        for f in changes.files:
            lines = (f.content.count('\n') + 1) if f.content is not None else 0
            file_summary.append(f"  - {f.path} ({f.action}, {lines} lines)")
        files_text = "\n".join(file_summary)

        prompt = (
            f"你是一个 Git commit message 生成器。\n"
            f"请根据以下信息生成一条简洁专业的 Git commit message。\n\n"
            f"## 规则\n"
            f"- 使用 Conventional Commits 格式: type(scope): 中文描述\n"
            f"- type 使用英文: feat/fix/refactor/chore 等\n"
            f"- scope 和描述使用中文\n"
            f"- 第一行不超过 72 字符\n"
            f"- 可以有正文部分，用中文列出关键变更\n"
            f"- 只输出 commit message 本身，不要其他内容\n\n"
            f"## 任务信息\n"
            f"- ID: {task.id}\n"
            f"- 标题: {task.title}\n"
            f"- 描述: {task.description[:300]}\n\n"
            f"## 变更文件\n{files_text}\n"
        )

        try:
            response = self._llm.call(
                system_prompt="你是 Git commit message 生成器，只输出 commit message，描述部分使用中文。",
                messages=[{"role": "user", "content": prompt}],
                label=f"CommitMsg/{task.id}",
            )
            msg = (response.content or "").strip()
            # 清理可能的 markdown 包裹
            if msg.startswith("```"):
                msg = msg.split("\n", 1)[-1]
            if msg.endswith("```"):
                msg = msg.rsplit("```", 1)[0]
            msg = msg.strip()
            if msg:
                return msg
        except Exception as e:
            logger.warning(f"LLM 生成 commit message 失败: {e}")

        # 降级：使用固定格式
        return f"feat({task.id}): {task.title}"
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
