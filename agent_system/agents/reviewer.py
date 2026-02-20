"""Reviewer Agent — 代码审查"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task, ReviewResult
from agent_system.agents.coder import CodeChanges
from agent_system.tools.run_command import run_command_tool, RUN_COMMAND_TOOL_DEFINITION
from agent_system.tools.read_file import READ_FILE_TOOL_DEFINITION
from agent_system.tools.grep_content import GREP_CONTENT_TOOL_DEFINITION
from agent_system.tools.diff_file import DIFF_FILE_TOOL_DEFINITION
from agent_system.tools.ts_check import TS_CHECK_TOOL_DEFINITION

logger = logging.getLogger(__name__)


class Reviewer(BaseAgent):
    """代码审查 Agent

    职责:
    - 执行项目配置中声明的 reviewCommands（如编译检查）
    - 根据 reviewChecklist 逐项检查代码质量
    - 失败时生成修复建议
    """

    def execute(
        self,
        task: Task,
        context: AgentContext,
        code_changes: CodeChanges | None = None,
        **kwargs: Any,
    ) -> ReviewResult:
        """执行代码审查

        LLM 会在工具循环中自主执行 reviewCommands 并检查代码质量。

        Args:
            task: 当前任务
            context: Agent 上下文
            code_changes: Coder 产出的代码变更

        Returns:
            ReviewResult 审查结果
        """
        self._active_conversation_log = kwargs.get("conversation_log")
        return self._llm_review(task, context, code_changes)

    def build_system_prompt(self, project: Any) -> str:
        """构建 Reviewer 系统提示词（公开方法，方便测试）

        Args:
            project: ProjectConfig 实例

        Returns:
            渲染后的系统提示词
        """
        template = self._load_prompt_template("reviewer.md")

        checklist_text = ""
        if hasattr(project, "review_checklist"):
            for i, item in enumerate(project.review_checklist, 1):
                checklist_text += f"{i}. {item}\n"

        commands_text = ""
        if hasattr(project, "review_commands"):
            commands_text = json.dumps(project.review_commands, ensure_ascii=False)

        conventions = getattr(project, "coding_conventions", "")

        return self._render_template(template, {
            "codingConventions": conventions or "无",
            "reviewChecklist": checklist_text or "无",
            "reviewCommands": commands_text or "无",
        })

    def _llm_review(
        self,
        task: Task,
        context: AgentContext,
        code_changes: CodeChanges | None,
    ) -> ReviewResult:
        """使用 LLM 进行代码质量检查

        Args:
            task: 当前任务
            context: Agent 上下文
            code_changes: 代码变更

        Returns:
            ReviewResult
        """
        system_prompt = self.build_system_prompt(context.project)

        # 构建用于审查的代码摘要
        code_summary = ""
        if code_changes and code_changes.files:
            for f in code_changes.files:
                code_summary += (
                    f"\n### {f.path} ({f.action})\n"
                    f"```\n{f.content[:2000]}\n```\n"
                )

        # 构建 review commands 列表
        review_cmds_text = ""
        if context.project.review_commands:
            cmds_list = "\n".join(
                f"  - `{cmd}`" for cmd in context.project.review_commands
            )
            review_cmds_text = (
                f"## 审查命令\n\n"
                f"请依次使用 `run_command` 工具执行以下命令，工作目录为 `{context.project.project_root}`:\n"
                f"{cmds_list}\n\n"
                f"如果命令失败，请分析输出并尝试修复（如安装缺失依赖等），然后重试。\n"
                f"如果确实无法修复，将失败信息记入 issues。\n\n"
            )

        user_message = (
            f"## 审查任务\n\n"
            f"**任务 ID**: {task.id}\n"
            f"**标题**: {task.title}\n\n"
            f"{review_cmds_text}"
            f"## 代码变更\n{code_summary}\n\n"
            f"## 要求\n\n"
            f"1. 先执行上述审查命令（如有），根据输出判断是否有编译/测试错误\n"
            f"2. 逐项检查代码质量\n"
            f"3. 输出 JSON 格式审查结果:\n"
            f'{{"passed": true/false, "issues": [...], "suggestions": [...]}}'
        )

        tools = [
            RUN_COMMAND_TOOL_DEFINITION,
            READ_FILE_TOOL_DEFINITION,
            GREP_CONTENT_TOOL_DEFINITION,
            DIFF_FILE_TOOL_DEFINITION,
            TS_CHECK_TOOL_DEFINITION,
        ]

        class ReviewToolExecutor:
            def execute(self, name: str, tool_input: dict[str, Any]) -> str:
                if name == "run_command":
                    result = run_command_tool(
                        command=tool_input["command"],
                        cwd=tool_input.get("cwd", context.project.project_root),
                        timeout=tool_input.get("timeout", 0),
                        stdin_input=tool_input.get("stdin_input"),
                    )
                    output = f"exit_code: {result.exit_code}\n"
                    if result.stdout:
                        output += f"stdout:\n{result.stdout}\n"
                    if result.stderr:
                        output += f"stderr:\n{result.stderr}\n"
                    return output

                elif name == "read_file":
                    from agent_system.tools.read_file import read_file_tool
                    try:
                        return read_file_tool(
                            path=tool_input["path"],
                            start=tool_input.get("start", 1),
                            end=tool_input.get("end"),
                        )
                    except FileNotFoundError as e:
                        return f"错误: {e}"

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

                elif name == "diff_file":
                    from agent_system.tools.diff_file import diff_file_tool
                    return diff_file_tool(
                        file_a=tool_input["file_a"],
                        file_b=tool_input["file_b"],
                        context_lines=tool_input.get("context_lines", 3),
                    )

                elif name == "ts_check":
                    from agent_system.tools.ts_check import ts_check_tool
                    # 强制使用实际项目路径，忽略 LLM 传入的路径
                    result = ts_check_tool(
                        project_root=context.project.project_root,
                        tsconfig=tool_input.get("tsconfig", "tsconfig.json"),
                    )
                    return json.dumps(result.to_dict(), ensure_ascii=False)

                return f"未知工具: {name}"

        response = self._llm.call_with_tools_loop(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            tool_executor=ReviewToolExecutor(),
            max_iterations=300,
            soft_limit=30,
            conversation_log=self._active_conversation_log,
            label=f"Reviewer/{task.id}",
        )

        return self._parse_review_result(response.content)

    def _parse_review_result(self, content: str) -> ReviewResult:
        """解析 LLM 输出的审查结果 JSON

        Args:
            content: LLM 输出

        Returns:
            ReviewResult
        """
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                return ReviewResult(
                    passed=False,
                    issues=["无法解析审查结果"],
                    suggestions=[],
                )
            data = json.loads(content[start:end])
            return ReviewResult(
                passed=data.get("passed", False),
                issues=data.get("issues", []),
                suggestions=data.get("suggestions", []),
            )
        except json.JSONDecodeError:
            return ReviewResult(
                passed=False,
                issues=["审查结果 JSON 解析失败"],
                suggestions=[],
            )
