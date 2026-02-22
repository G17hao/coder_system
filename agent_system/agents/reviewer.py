"""Reviewer Agent — 代码审查"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_system.agents.base import BaseAgent
from agent_system.models.context import AgentContext
from agent_system.models.task import Task, ReviewResult
from agent_system.agents.coder import CodeChanges
from agent_system.services.path_guard import PathGuard
from agent_system.tools.run_command import (
    run_command_tool,
    send_stdin_tool,
    RUN_COMMAND_TOOL_DEFINITION,
    SEND_STDIN_TOOL_DEFINITION,
)
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
        prompt_overrides = getattr(project, "prompt_overrides", {}) or {}
        project_specific_prompt = str(prompt_overrides.get("reviewer", "")).strip()

        return self._render_template(template, {
            "codingConventions": conventions or "无",
            "reviewChecklist": checklist_text or "无",
            "reviewCommands": commands_text or "无",
            "projectSpecificPrompt": project_specific_prompt or "无",
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
        guard = PathGuard(
            allowed_roots=[context.project.project_root] + list(context.project.reference_roots),
            default_base_dir=context.project.project_root,
        )

        # 构建用于审查的代码摘要和变更文件列表
        code_summary = ""
        changed_files_list = ""
        review_files_list = ""
        if code_changes and code_changes.files:
            file_paths = [f.path for f in code_changes.files]
            changed_files_list = "\n".join(
                f"  - `{p}` ({f.action})" for p, f in zip(file_paths, code_changes.files)
            )

            review_paths: list[str] = []
            if code_changes.review_files:
                for p in code_changes.review_files:
                    path = str(p).strip()
                    if path:
                        review_paths.append(path)
            else:
                review_paths = list(file_paths)

            seen: set[str] = set()
            review_paths = [p for p in review_paths if not (p in seen or seen.add(p))]
            review_files_list = "\n".join(f"  - `{p}`" for p in review_paths)

            actions_by_path = {f.path: f.action for f in code_changes.files}

            for path in review_paths:
                resolved = guard.resolve_path(path)
                action = actions_by_path.get(path, "context")
                content_preview = ""
                if not guard.is_allowed(resolved):
                    content_preview = f"[路径约束] 文件路径不在允许范围: {resolved}"
                elif resolved.exists():
                    try:
                        file_content = resolved.read_text(encoding="utf-8", errors="replace")
                        content_preview = file_content[:2000]
                    except Exception as e:
                        content_preview = f"[读取失败] {type(e).__name__}: {e}"
                else:
                    content_preview = "[文件不存在且无内联内容]"

                code_summary += (
                    f"\n### {path} ({action})\n"
                    f"```\n{content_preview}\n```\n"
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
                f"执行完命令后：\n"
                f"- **编译错误（tsc）和测试失败（vitest）**：无论发生在哪个文件都必须上报，Coder 的改动可能破坏了未变更的调用方\n"
                f"- **代码风格问题**（console.log、缺少注释等）：只报告变更文件列表中的文件，其他文件忽略\n\n"
            )

        # 如果 coder 没有产出任何文件，直接 PASS
        if not code_changes or not code_changes.files:
            logger.info("  [审查] Coder 无文件产出，自动通过")
            return ReviewResult(passed=True, issues=[], suggestions=[], context_for_coder="")

        changed_files_section = (
            f"## 本次变更文件列表\n\n"
            f"以下是本次 Coder 产出的文件（代码风格检查仅限这些文件）：\n"
            f"{changed_files_list}\n\n"
        )
        review_files_section = ""
        if review_files_list:
            review_files_section = (
                f"## 需要通读的文件清单\n\n"
                f"以下文件需要审查其完整内容（可包含未改动文件）：\n"
                f"{review_files_list}\n\n"
            )

        user_message = (
            f"## 审查任务\n\n"
            f"**任务 ID**: {task.id}\n"
            f"**标题**: {task.title}\n\n"
            f"{changed_files_section}"
            f"{review_files_section}"
            f"{review_cmds_text}"
            f"## 代码变更（系统已从磁盘预加载）\n{code_summary}\n\n"
            f"## 要求\n\n"
            f"1. 先执行上述审查命令（如有）：\n"
            f"   - 编译错误和测试失败：**全项目范围**，有报错即为 issue（Coder 可能破坏了调用方）\n"
            f"   - 代码风格问题：**只检查变更文件列表中的文件**，其他文件的风格问题忽略\n"
            f"2. 优先基于上面的预加载代码内容进行审查；若信息不足再使用 read_file 补充读取\n"
            f"3. **不要尝试修复任何代码**，只报告发现的问题\n"
            f"4. 尽快输出 JSON 格式审查结果（审查应在 10 轮工具调用内完成）:\n"
            f'{{"passed": true/false, "issues": [...], "suggestions": [...]}}'
        )

        tools = [
            RUN_COMMAND_TOOL_DEFINITION,
            SEND_STDIN_TOOL_DEFINITION,
            READ_FILE_TOOL_DEFINITION,
            GREP_CONTENT_TOOL_DEFINITION,
            DIFF_FILE_TOOL_DEFINITION,
            TS_CHECK_TOOL_DEFINITION,
        ]

        class ReviewToolExecutor:
            def __init__(self, path_guard: PathGuard) -> None:
                self._guard = path_guard

            def execute(self, name: str, tool_input: dict[str, Any]) -> str:
                if name == "run_command":
                    result = run_command_tool(
                        command=tool_input["command"],
                        cwd=tool_input.get("cwd", context.project.project_root),
                        timeout=tool_input.get("timeout", 0),
                        stdin_input=tool_input.get("stdin_input"),
                        interactive=tool_input.get("interactive", False),
                        idle_timeout=tool_input.get("idle_timeout", 10.0),
                    )
                    output = f"exit_code: {result.exit_code}\n"
                    if result.process_id:
                        output += f"process_id: {result.process_id} (进程仍在运行，等待输入。使用 send_stdin 发送输入)\n"
                    if result.stdout:
                        output += f"stdout:\n{result.stdout}\n"
                    if result.stderr:
                        output += f"stderr:\n{result.stderr}\n"
                    return output

                elif name == "send_stdin":
                    result = send_stdin_tool(
                        process_id=tool_input["process_id"],
                        input_text=tool_input["input_text"],
                        idle_timeout=tool_input.get("idle_timeout", 10.0),
                    )
                    output = f"exit_code: {result.exit_code}\n"
                    if result.process_id:
                        output += f"process_id: {result.process_id} (进程仍在运行)\n"
                    if result.stdout:
                        output += f"stdout:\n{result.stdout}\n"
                    if result.stderr:
                        output += f"stderr:\n{result.stderr}\n"
                    return output

                elif name == "read_file":
                    from agent_system.tools.read_file import read_file_tool, read_files_tool
                    try:
                        if "requests" in tool_input:
                            for req in tool_input.get("requests", []):
                                req_path = req.get("path")
                                if isinstance(req_path, str):
                                    normalized, err = self._guard.validate_file(req_path)
                                    if err:
                                        return f"错误: {err}"
                                    req["path"] = normalized
                        elif "paths" in tool_input:
                            normalized_paths: list[str] = []
                            for req_path in tool_input.get("paths", []):
                                if not isinstance(req_path, str):
                                    continue
                                normalized, err = self._guard.validate_file(req_path)
                                if err:
                                    return f"错误: {err}"
                                normalized_paths.append(normalized)
                            tool_input["paths"] = normalized_paths
                        elif "path" in tool_input:
                            normalized, err = self._guard.validate_file(tool_input["path"])
                            if err:
                                return f"错误: {err}"
                            tool_input["path"] = normalized

                        if "requests" in tool_input or "paths" in tool_input:
                            return read_files_tool(
                                requests=tool_input.get("requests"),
                                paths=tool_input.get("paths"),
                                start=tool_input.get("start", 1),
                                end=tool_input.get("end"),
                            )
                        return read_file_tool(
                            path=tool_input["path"],
                            start=tool_input.get("start", 1),
                            end=tool_input.get("end"),
                        )
                    except FileNotFoundError as e:
                        return f"错误: {e}"

                elif name == "grep_content":
                    from agent_system.tools.grep_content import grep_content_tool, grep_dir_tool
                    target = self._guard.resolve_path(str(tool_input["path"]))
                    if not self._guard.is_allowed(target):
                        if self._guard.default_base is None:
                            return json.dumps([{"line": 0, "content": f"[路径约束] 路径不在允许范围: {target}"}], ensure_ascii=False)
                        target = self._guard.default_base
                    if target.is_dir():
                        results = grep_dir_tool(
                            base_dir=str(target),
                            pattern=tool_input["pattern"],
                            file_pattern=tool_input.get("file_pattern", "*.ts"),
                            max_matches=tool_input.get("max_matches", 50),
                        )
                    else:
                        results = grep_content_tool(
                            path=str(target),
                            pattern=tool_input["pattern"],
                            max_matches=tool_input.get("max_matches", 50),
                        )
                    return json.dumps(results, ensure_ascii=False)

                elif name == "diff_file":
                    from agent_system.tools.diff_file import diff_file_tool
                    file_a, err_a = self._guard.validate_file(str(tool_input["file_a"]))
                    if err_a or file_a is None:
                        return f"错误: {err_a or 'file_a 路径无效'}"
                    file_b, err_b = self._guard.validate_file(str(tool_input["file_b"]))
                    if err_b or file_b is None:
                        return f"错误: {err_b or 'file_b 路径无效'}"
                    return diff_file_tool(
                        file_a=file_a,
                        file_b=file_b,
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
            tool_executor=ReviewToolExecutor(path_guard=guard),
            max_iterations=30,
            soft_limit=10,
            conversation_log=self._active_conversation_log,
            label=f"Reviewer/{task.id}",
        )

        return self._parse_review_result(response.content)

    def _parse_review_result(self, content: str) -> ReviewResult:
        """解析 LLM 输出的审查结果 JSON

        支持多种情况：
        1. 正常 JSON 输出
        2. JSON 包裹在 markdown code block 中
        3. 纯文本回退（尝试关键字判断）

        Args:
            content: LLM 输出

        Returns:
            ReviewResult
        """
        if not content or not content.strip():
            logger.warning("Reviewer LLM 输出为空")
            return ReviewResult(
                passed=False,
                issues=["Reviewer LLM 输出为空，无法解析审查结果"],
                suggestions=[],
            )

        # 尝试提取 markdown code block 中的 JSON
        import re
        code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if code_block_match:
            try:
                data = json.loads(code_block_match.group(1))
                return ReviewResult(
                    passed=data.get("passed", False),
                    issues=data.get("issues", []),
                    suggestions=data.get("suggestions", []),
                    context_for_coder=data.get("context_for_coder", ""),
                )
            except json.JSONDecodeError:
                pass

        # 尝试直接提取 JSON 对象
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end > start:
                data = json.loads(content[start:end])
                return ReviewResult(
                    passed=data.get("passed", False),
                    issues=data.get("issues", []),
                    suggestions=data.get("suggestions", []),
                    context_for_coder=data.get("context_for_coder", ""),
                )
        except json.JSONDecodeError:
            pass

        # 回退：根据关键字推断
        content_lower = content.lower()
        if '"passed": true' in content_lower or '"passed":true' in content_lower:
            return ReviewResult(passed=True, issues=[], suggestions=[], context_for_coder="")

        # 无法解析，记录原始内容前 200 字帮助调试
        preview = content[:200].replace("\n", " ")
        logger.warning(f"无法解析审查结果，LLM 输出前200字: {preview}")
        return ReviewResult(
            passed=False,
            issues=[f"无法解析审查结果（LLM 输出前200字: {preview}）"],
            suggestions=["检查 Reviewer LLM 是否正确输出了 JSON 格式的审查结果"],
            context_for_coder="",
        )
