"""Coder Agent — 代码生成与修改"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task
from agent_system.tools.read_file import READ_FILE_TOOL_DEFINITION
from agent_system.tools.search_file import SEARCH_FILE_TOOL_DEFINITION
from agent_system.tools.write_file import WRITE_FILE_TOOL_DEFINITION
from agent_system.tools.grep_content import GREP_CONTENT_TOOL_DEFINITION
from agent_system.tools.list_directory import LIST_DIRECTORY_TOOL_DEFINITION
from agent_system.tools.replace_in_file import REPLACE_IN_FILE_TOOL_DEFINITION
from agent_system.tools.todo_list import TODO_LIST_TOOL_DEFINITION


@dataclass
class FileChange:
    """单个文件变更"""
    path: str
    content: str
    action: str = "create"  # "create" | "modify"


@dataclass
class CodeChanges:
    """Coder 输出的文件变更集"""
    files: list[FileChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files": [
                {"path": f.path, "content": f.content, "action": f.action}
                for f in self.files
            ]
        }

    @classmethod
    def from_dict(cls, data: dict) -> CodeChanges:
        files = [
            FileChange(
                path=f["path"],
                content=f["content"],
                action=f.get("action", "create"),
            )
            for f in data.get("files", [])
        ]
        return cls(files=files)

    @classmethod
    def from_json(cls, json_str: str) -> CodeChanges:
        """从 JSON 字符串解析，支持从 LLM 输出中提取"""
        # 尝试从输出中提取 JSON 块
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return cls(files=[])
        try:
            data = json.loads(json_str[start:end])
            return cls.from_dict(data)
        except json.JSONDecodeError:
            return cls(files=[])


class CoderToolExecutor:
    """Coder 可用的工具执行器

    自动跟踪 write_file / replace_in_file 实际写入的文件内容，
    用于生成可靠的 CodeChanges，不依赖 LLM 最终 JSON 输出。
    """

    def __init__(self) -> None:
        # path → {"content": str, "action": "create"|"modify"}
        self._tracked_writes: dict[str, dict[str, str]] = {}
        # TODO 列表状态（跨工具调用持久）
        self._todo_items: list[dict[str, Any]] = []

    @property
    def tracked_changes(self) -> list[dict[str, str]]:
        """返回工具循环中实际写入的文件列表"""
        return [
            {"path": path, "content": info["content"], "action": info["action"]}
            for path, info in self._tracked_writes.items()
        ]

    def execute(self, name: str, tool_input: dict[str, Any]) -> str:
        try:
            return self._dispatch(name, tool_input)
        except Exception as e:
            return f"错误: 工具 {name} 执行异常: {type(e).__name__}: {e}"

    def _dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        """分发工具调用到具体实现"""
        if name == "read_file":
            from agent_system.tools.read_file import read_file_tool
            return read_file_tool(
                path=tool_input["path"],
                start=tool_input.get("start", 1),
                end=tool_input.get("end"),
            )

        elif name == "search_file":
            from agent_system.tools.search_file import search_file_tool
            patterns = tool_input.get("patterns")
            if patterns:
                # 多模式批量搜索
                combined: dict[str, list[str]] = {}
                for pat in patterns:
                    results = search_file_tool(
                        base_dir=tool_input["base_dir"],
                        pattern=pat,
                        regex=tool_input.get("regex"),
                        max_results=tool_input.get("max_results", 200),
                        respect_gitignore=tool_input.get("respect_gitignore", True),
                    )
                    combined[pat] = results
                return json.dumps(combined, ensure_ascii=False)
            else:
                results = search_file_tool(
                    base_dir=tool_input["base_dir"],
                    pattern=tool_input.get("pattern", "*"),
                    regex=tool_input.get("regex"),
                    max_results=tool_input.get("max_results", 200),
                    respect_gitignore=tool_input.get("respect_gitignore", True),
                )
                return json.dumps(results, ensure_ascii=False)

        elif name == "write_file":
            from agent_system.tools.write_file import write_file_tool
            file_path = tool_input["path"]
            content = tool_input["content"]
            result = write_file_tool(path=file_path, content=content)
            # 跟踪写入：如果路径已有记录视为 modify，否则判断文件是否预先存在
            action = "modify" if file_path in self._tracked_writes else "create"
            self._tracked_writes[file_path] = {"content": content, "action": action}
            return result

        elif name == "grep_content":
            from agent_system.tools.grep_content import grep_content_tool, grep_dir_tool
            from pathlib import Path
            target = Path(tool_input["path"])
            if target.is_dir():
                results = grep_dir_tool(
                    base_dir=tool_input["path"],
                    pattern=tool_input["pattern"],
                    file_pattern=tool_input.get("file_pattern", "*.ts"),
                    max_matches=tool_input.get("max_matches", 50),
                )
            else:
                results = grep_content_tool(
                    path=tool_input["path"],
                    pattern=tool_input["pattern"],
                    max_matches=tool_input.get("max_matches", 50),
                )
            return json.dumps(results, ensure_ascii=False)

        elif name == "list_directory":
            from agent_system.tools.list_directory import list_directory_tool
            return list_directory_tool(
                path=tool_input["path"],
                max_depth=tool_input.get("max_depth", 3),
                include_files=tool_input.get("include_files", True),
                max_entries=tool_input.get("max_entries", 500),
                respect_gitignore=tool_input.get("respect_gitignore", True),
            )

        elif name == "replace_in_file":
            from agent_system.tools.replace_in_file import replace_in_file_tool
            file_path = tool_input["path"]
            result = replace_in_file_tool(
                path=file_path,
                old_text=tool_input["old_text"],
                new_text=tool_input["new_text"],
            )
            # replace 后重新读取文件完整内容以跟踪
            from pathlib import Path
            updated_content = Path(file_path).read_text(encoding="utf-8")
            self._tracked_writes[file_path] = {"content": updated_content, "action": "modify"}
            return result

        elif name == "todo_list":
            from agent_system.tools.todo_list import todo_list_tool
            return todo_list_tool(
                operation=tool_input["operation"],
                items=tool_input.get("items"),
                _state=self._todo_items,
            )

        return f"未知工具: {name}"


class Coder(BaseAgent):
    """代码生成 Agent

    职责:
    - 根据 Analyst 报告生成/修改代码文件
    - 严格遵循编码规范（运行时从 project.json 注入）
    - 输出精确的文件变更
    """

    def execute(
        self,
        task: Task,
        context: AgentContext,
        analysis_report: str = "",
        **kwargs: Any,
    ) -> CodeChanges:
        """生成代码变更

        Args:
            task: 当前任务
            context: Agent 上下文
            analysis_report: Analyst 输出的分析报告

        Returns:
            CodeChanges 文件变更集合
        """
        system_prompt = self.build_system_prompt(context.project, context=context)
        user_message = self._build_user_message(task, analysis_report, context)

        tools = [
            TODO_LIST_TOOL_DEFINITION,
            READ_FILE_TOOL_DEFINITION,
            SEARCH_FILE_TOOL_DEFINITION,
            WRITE_FILE_TOOL_DEFINITION,
            GREP_CONTENT_TOOL_DEFINITION,
            LIST_DIRECTORY_TOOL_DEFINITION,
            REPLACE_IN_FILE_TOOL_DEFINITION,
        ]
        tool_executor = CoderToolExecutor()

        response = self._llm.call_with_tools_loop(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            tool_executor=tool_executor,
            max_iterations=300,
            soft_limit=100,
            conversation_log=kwargs.get("conversation_log"),
            label=f"Coder/{task.id}",
        )

        # 优先使用工具循环中实际写入的文件记录（可靠），
        # 而非 LLM 最终 JSON 输出（可能包含占位符）
        tracked = tool_executor.tracked_changes
        if tracked:
            return CodeChanges.from_dict({"files": tracked})
        # 回退：如果工具循环未写入任何文件，尝试解析 LLM 输出
        return CodeChanges.from_json(response.content)

    def build_system_prompt(
        self,
        project: Any,
        context: AgentContext | None = None,
    ) -> str:
        """构建 Coder 系统提示词（公开方法，方便测试）

        Args:
            project: ProjectConfig 实例
            context: Agent 上下文（可选，用于获取已完成任务信息）

        Returns:
            渲染后的系统提示词
        """
        template = self._load_prompt_template("coder.md")

        # 格式化模式映射
        mappings_text = ""
        if hasattr(project, "pattern_mappings"):
            for m in project.pattern_mappings:
                mappings_text += f"- {m.from_pattern} → {m.to_pattern}\n"

        conventions = getattr(project, "coding_conventions", "")

        # 格式化已完成任务上下文
        completed_text = self._format_completed_tasks(context)

        return self._render_template(template, {
            "codingConventions": conventions,
            "patternMappings": mappings_text or "无",
            "completedTasks": completed_text or "无（首个任务）",
        })

    @staticmethod
    def _format_completed_tasks(context: AgentContext | None) -> str:
        """格式化已完成任务列表"""
        if context is None or not context.completed_tasks:
            return ""
        lines: list[str] = []
        for task_id, task in sorted(context.completed_tasks.items()):
            lines.append(f"- [{task.id}] {task.title}: {task.description[:80]}")
        return "\n".join(lines)

    def _build_user_message(
        self,
        task: Task,
        analysis_report: str,
        context: AgentContext,
    ) -> str:
        """构建用户消息"""
        retry_info = ""
        if task.retry_count > 0 and task.review_result:
            supervisor_hint_text = ""
            supervisor_plan_text = ""
            reviewer_context_text = ""
            if task.supervisor_hint:
                supervisor_hint_text = (
                    f"\n**[Supervisor 修复指引]** 请优先执行以下修复方向：\n"
                    f"{task.supervisor_hint}\n"
                )
            if task.supervisor_plan:
                supervisor_plan_text = (
                    f"\n**[Supervisor 重规划]** 以下是监督阶段输出的执行计划，"
                    f"请严格按清单推进；仅在计划中的未知项上补充信息获取：\n"
                    f"{task.supervisor_plan}\n"
                )
            if task.review_result.context_for_coder.strip():
                reviewer_context_text = (
                    f"\n**[Reviewer 上下文摘要]** 以下信息由 Reviewer 已确认，"
                    f"请优先复用，避免重复信息获取：\n"
                    f"{task.review_result.context_for_coder}\n"
                )
            retry_info = (
                f"\n## 上次审查失败信息（重试 {task.retry_count}）\n\n"
                f"**重要：上次编写的代码文件仍保留在磁盘上，你只需要修复下面的问题，不要从头重写所有文件。**\n"
                f"请先用 read_file 查看相关文件当前内容，然后用 replace_in_file 精确修复问题部分。\n"
                f"{supervisor_hint_text}\n"
                f"{supervisor_plan_text}\n"
                f"{reviewer_context_text}\n"
                f"问题: {json.dumps(task.review_result.issues, ensure_ascii=False)}\n"
                f"建议: {json.dumps(task.review_result.suggestions, ensure_ascii=False)}\n"
            )

        return (
            f"## 当前任务\n\n"
            f"**ID**: {task.id}\n"
            f"**标题**: {task.title}\n"
            f"**描述**: {task.description}\n\n"
            f"## 分析报告\n\n{analysis_report}\n\n"
            f"## 项目根目录\n\n{context.project.project_root}\n"
            f"{retry_info}\n"
            f"## 要求\n\n"
            f"请根据分析报告生成代码文件。输出 JSON 格式的文件变更列表:\n"
            f'{{"files": [{{"path": "相对路径", "action": "create|modify", "content": "完整内容"}}]}}\n'
        )
