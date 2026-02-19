"""Analyst Agent — 代码分析，为 Coder 提供精确规格"""

from __future__ import annotations

import json
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task
from agent_system.tools.read_file import READ_FILE_TOOL_DEFINITION
from agent_system.tools.search_file import SEARCH_FILE_TOOL_DEFINITION
from agent_system.tools.grep_content import GREP_CONTENT_TOOL_DEFINITION
from agent_system.tools.list_directory import LIST_DIRECTORY_TOOL_DEFINITION
from agent_system.tools.project_structure import GET_PROJECT_STRUCTURE_TOOL_DEFINITION


class AnalystToolExecutor:
    """Analyst 可用的工具执行器"""

    def execute(self, name: str, tool_input: dict[str, Any]) -> str:
        """执行工具调用

        Args:
            name: 工具名称
            tool_input: 工具参数

        Returns:
            工具执行结果字符串
        """
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
            results = search_file_tool(
                base_dir=tool_input["base_dir"],
                pattern=tool_input.get("pattern", "*"),
                regex=tool_input.get("regex"),
                max_results=tool_input.get("max_results", 200),
                respect_gitignore=tool_input.get("respect_gitignore", True),
            )
            return json.dumps(results, ensure_ascii=False)

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

        elif name == "get_project_structure":
            from agent_system.tools.project_structure import get_project_structure_tool
            return get_project_structure_tool(
                project_root=tool_input["project_root"],
                source_dirs=tool_input.get("source_dirs"),
                extensions=tool_input.get("extensions"),
            )

        return f"未知工具: {name}"


class Analyst(BaseAgent):
    """代码分析 Agent

    职责:
    - 读取参考代码（reference_roots），提取接口、数据结构、事件流
    - 读取目标项目代码（project_root），识别已有实现和缺口
    - 输出结构化分析报告
    """

    def execute(self, task: Task, context: AgentContext, **kwargs: Any) -> str:
        """执行代码分析

        Args:
            task: 当前任务
            context: Agent 上下文

        Returns:
            结构化分析报告（JSON 字符串）
        """
        system_prompt = self._build_system_prompt(context)
        user_message = self._build_user_message(task, context)

        tools = [
            READ_FILE_TOOL_DEFINITION,
            SEARCH_FILE_TOOL_DEFINITION,
            GREP_CONTENT_TOOL_DEFINITION,
            LIST_DIRECTORY_TOOL_DEFINITION,
            GET_PROJECT_STRUCTURE_TOOL_DEFINITION,
        ]
        tool_executor = AnalystToolExecutor()

        response = self._llm.call_with_tools_loop(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            tool_executor=tool_executor,
            max_iterations=300,
            soft_limit=30,
            conversation_log=kwargs.get("conversation_log"),
        )

        return response.content

    def _build_system_prompt(self, context: AgentContext) -> str:
        """构建 Analyst 系统提示词"""
        template = self._load_prompt_template("analyst.md")

        # 格式化模式映射
        mappings_text = ""
        for m in context.project.pattern_mappings:
            mappings_text += f"- {m.from_pattern} → {m.to_pattern}\n"

        # 格式化已完成任务上下文
        completed_text = self._format_completed_tasks(context)

        conventions = getattr(context.project, "coding_conventions", "")

        return self._render_template(template, {
            "projectDescription": context.project.project_description,
            "codingConventions": conventions or "无",
            "patternMappings": mappings_text or "无",
            "completedTasks": completed_text or "无（首个任务）",
        })

    @staticmethod
    def _format_completed_tasks(context: AgentContext) -> str:
        """格式化已完成任务列表，供 prompt 注入"""
        if not context.completed_tasks:
            return ""
        lines: list[str] = []
        for task_id, task in sorted(context.completed_tasks.items()):
            lines.append(f"- [{task.id}] {task.title}: {task.description[:80]}")
        return "\n".join(lines)

    def _build_user_message(self, task: Task, context: AgentContext) -> str:
        """构建用户消息"""
        reference_roots = "\n".join(
            f"- {r}" for r in context.project.reference_roots
        )
        return (
            f"## 当前任务\n\n"
            f"**ID**: {task.id}\n"
            f"**标题**: {task.title}\n"
            f"**描述**: {task.description}\n"
            f"**分类**: {task.category}\n\n"
            f"## 项目目录\n\n"
            f"- 目标项目: {context.project.project_root}\n"
            f"- 参考代码:\n{reference_roots}\n\n"
            f"## 要求\n\n"
            f"请使用 read_file 和 search_file 工具分析参考代码和目标项目代码，"
            f"然后输出结构化分析报告（JSON），包含：\n"
            f"- interfaces: 需要的接口定义\n"
            f"- methods: 需要实现的方法签名\n"
            f"- events: 需要处理的事件\n"
            f"- files: 需要创建/修改的文件清单\n"
            f"- gaps: 目标项目中的缺口\n"
        )
