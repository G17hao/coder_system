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
    """Coder 可用的工具执行器"""

    def execute(self, name: str, tool_input: dict[str, Any]) -> str:
        if name == "read_file":
            from agent_system.tools.read_file import read_file_tool
            try:
                return read_file_tool(
                    path=tool_input["path"],
                    start=tool_input.get("start", 1),
                    end=tool_input.get("end"),
                )
            except FileNotFoundError as e:
                return f"错误: {e}"

        elif name == "search_file":
            from agent_system.tools.search_file import search_file_tool
            results = search_file_tool(
                base_dir=tool_input["base_dir"],
                pattern=tool_input.get("pattern", "*"),
                regex=tool_input.get("regex"),
            )
            return json.dumps(results, ensure_ascii=False)

        elif name == "write_file":
            from agent_system.tools.write_file import write_file_tool
            return write_file_tool(
                path=tool_input["path"],
                content=tool_input["content"],
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
            READ_FILE_TOOL_DEFINITION,
            SEARCH_FILE_TOOL_DEFINITION,
            WRITE_FILE_TOOL_DEFINITION,
        ]
        tool_executor = CoderToolExecutor()

        response = self._llm.call_with_tools_loop(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            tool_executor=tool_executor,
            max_iterations=15,
        )

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
            retry_info = (
                f"\n## 上次审查失败信息\n\n"
                f"问题: {json.dumps(task.review_result.issues, ensure_ascii=False)}\n"
                f"建议: {json.dumps(task.review_result.suggestions, ensure_ascii=False)}\n"
                f"请根据以上反馈修复代码。\n"
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
