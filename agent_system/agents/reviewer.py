"""Reviewer Agent — 代码审查"""

from __future__ import annotations

import json
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

        Args:
            task: 当前任务
            context: Agent 上下文
            code_changes: Coder 产出的代码变更

        Returns:
            ReviewResult 审查结果
        """
        self._active_conversation_log = kwargs.get("conversation_log")

        # 1. 执行 reviewCommands
        command_issues = self._run_review_commands(context)

        # 2. 使用 LLM 进行代码质量检查
        llm_review = self._llm_review(task, context, code_changes)

        # 3. 合并结果
        all_issues = command_issues + llm_review.issues
        passed = len(all_issues) == 0 and llm_review.passed

        return ReviewResult(
            passed=passed,
            issues=all_issues,
            suggestions=llm_review.suggestions,
        )

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

    def _run_review_commands(self, context: AgentContext) -> list[str]:
        """执行审查命令，收集失败信息

        Args:
            context: Agent 上下文

        Returns:
            失败的命令输出列表
        """
        issues: list[str] = []
        for cmd in context.project.review_commands:
            result = run_command_tool(
                command=cmd,
                cwd=context.project.project_root,
            )
            if not result.success:
                issues.append(
                    f"命令 `{cmd}` 失败 (exit={result.exit_code}):\n{result.stderr or result.stdout}"
                )
        return issues

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

        user_message = (
            f"## 审查任务\n\n"
            f"**任务 ID**: {task.id}\n"
            f"**标题**: {task.title}\n\n"
            f"## 代码变更\n{code_summary}\n\n"
            f"## 要求\n\n"
            f"请逐项检查代码质量，输出 JSON 格式审查结果:\n"
            f'{{"passed": true/false, "issues": [...], "suggestions": [...]}}'
        )

        tools = [
            READ_FILE_TOOL_DEFINITION,
            GREP_CONTENT_TOOL_DEFINITION,
            DIFF_FILE_TOOL_DEFINITION,
            TS_CHECK_TOOL_DEFINITION,
        ]

        class ReviewToolExecutor:
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
