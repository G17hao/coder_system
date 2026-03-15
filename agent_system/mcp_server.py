"""MCP Server — 暴露 Agent 系统工具给外部调用

用法:
    python -m agent_system.mcp_server
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP

    # 创建 MCP Server
    mcp = FastMCP("Agent System Tools")

    # ========================================================================
    # 文件操作工具
    # ========================================================================

    @mcp.tool()
    def read_file(path: str, start: int = 1, end: int | None = None) -> str:
        """读取文件内容

        Args:
            path: 文件路径（绝对路径或相对于项目根目录）
            start: 起始行号（从 1 开始）
            end: 结束行号（None 表示读到文件尾）

        Returns:
            文件内容字符串
        """
        from agent_system.tools.read_file import read_file_tool
        try:
            return read_file_tool(path, start, end)
        except FileNotFoundError as e:
            return f"Error: {e}"

    @mcp.tool()
    def write_file(path: str, content: str, append: bool = False) -> str:
        """写入文件内容

        Args:
            path: 文件路径
            content: 文件内容
            append: 是否追加模式（默认覆盖）

        Returns:
            操作结果（成功/失败信息）
        """
        from agent_system.tools.write_file import write_file_tool
        try:
            write_file_tool(path, content, append)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_directory(path: str, pattern: str | None = None) -> str:
        """列出目录内容

        Args:
            path: 目录路径
            pattern: 文件名匹配模式（如 *.ts）

        Returns:
            目录内容列表（JSON 格式）
        """
        from agent_system.tools.list_directory import list_directory_tool
        try:
            result = list_directory_tool(path, pattern)
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def search_file(pattern: str, root: str | None = None) -> str:
        """搜索文件

        Args:
            pattern: 文件名模式（支持 glob，如 **/*.ts）
            root: 搜索根目录（None 表示项目根目录）

        Returns:
            匹配的文件路径列表（JSON 格式）
        """
        from agent_system.tools.search_file import search_file_tool
        try:
            result = search_file_tool(pattern, root)
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def grep_content(pattern: str, path: str, max_matches: int = 50) -> str:
        """在文件中搜索内容

        Args:
            pattern: 搜索模式（正则表达式）
            path: 文件或目录路径
            max_matches: 最大匹配数

        Returns:
            匹配结果（JSON 格式）
        """
        from agent_system.tools.grep_content import grep_content_tool
        try:
            result = grep_content_tool(path, pattern, max_matches)
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def diff_file(file_a: str, file_b: str, context_lines: int = 3) -> str:
        """比较两个文件的差异

        Args:
            file_a: 文件 A 路径
            file_b: 文件 B 路径
            context_lines: 上下文行数

        Returns:
            Diff 输出
        """
        from agent_system.tools.diff_file import diff_file_tool
        try:
            return diff_file_tool(file_a, file_b, context_lines)
        except Exception as e:
            return f"Error: {e}"

    # ========================================================================
    # 命令执行工具
    # ========================================================================

    @mcp.tool()
    def run_command(command: str, cwd: str | None = None, timeout: int = 0) -> str:
        """执行 shell 命令

        Args:
            command: 要执行的命令
            cwd: 工作目录（None 表示项目根目录）
            timeout: 超时秒数（0 表示无限制）

        Returns:
            命令输出（stdout + stderr）
        """
        from agent_system.tools.run_command import run_command_tool
        try:
            result = run_command_tool(command, cwd, timeout)
            output = f"exit_code: {result.exit_code}\n"
            if result.stdout:
                output += f"stdout:\n{result.stdout}\n"
            if result.stderr:
                output += f"stderr:\n{result.stderr}\n"
            return output
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def ts_check(project_root: str, tsconfig: str = "tsconfig.json") -> str:
        """TypeScript 编译检查

        Args:
            project_root: 项目根目录
            tsconfig: tsconfig 文件名

        Returns:
            检查结果（JSON 格式）
        """
        from agent_system.tools.ts_check import ts_check_tool
        try:
            result = ts_check_tool(project_root, tsconfig)
            import json
            return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    # ========================================================================
    # 项目管理工具
    # ========================================================================

    @mcp.tool()
    def get_project_structure(root: str, max_depth: int = 3) -> str:
        """获取项目结构树

        Args:
            root: 项目根目录
            max_depth: 最大深度

        Returns:
            项目结构树文本
        """
        from agent_system.tools.project_structure import get_project_structure_tool
        try:
            return get_project_structure_tool(root, max_depth)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_todo_items(project_root: str) -> str:
        """列出项目中的 TODO 注释

        Args:
            project_root: 项目根目录

        Returns:
            TODO 列表（JSON 格式）
        """
        from agent_system.tools.todo_list import list_todo_items_tool
        try:
            result = list_todo_items_tool(project_root)
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    # ========================================================================
    # 资源（只读数据）
    # ========================================================================

    @mcp.resource("project://config")
    def get_project_config() -> str:
        """获取项目配置信息"""
        return "Agent System MCP Server - 提供文件操作、命令执行、代码检查等工具"

    @mcp.resource("project://tools")
    def list_available_tools() -> str:
        """列出所有可用工具"""
        tools = [
            "read_file - 读取文件",
            "write_file - 写入文件",
            "list_directory - 列出目录",
            "search_file - 搜索文件",
            "grep_content - 搜索内容",
            "diff_file - 比较文件差异",
            "run_command - 执行命令",
            "ts_check - TypeScript 检查",
            "get_project_structure - 获取项目结构",
            "list_todo_items - 列出 TODO",
        ]
        return "\n".join(f"- {tool}" for tool in tools)

    # ========================================================================
    # Prompt 模板
    # ========================================================================

    @mcp.prompt()
    def code_review(code: str, language: str = "typescript") -> str:
        """代码审查 Prompt 模板

        Args:
            code: 待审查的代码
            language: 编程语言

        Returns:
            格式化的审查请求
        """
        return f"""请审查以下 {language} 代码：

```{language}
{code}
```

请检查：
1. 代码风格和最佳实践
2. 潜在的错误和边界情况
3. 性能优化建议
4. 安全性问题
"""

    @mcp.prompt()
    def implement_feature(description: str, context: str = "") -> str:
        """功能实现 Prompt 模板

        Args:
            description: 功能描述
            context: 上下文信息

        Returns:
            格式化的实现请求
        """
        prompt = f"请实现以下功能：\n\n{description}"
        if context:
            prompt += f"\n\n上下文信息：\n{context}"
        return prompt

    # ========================================================================
    # 入口点
    # ========================================================================

    if __name__ == "__main__":
        # 运行 MCP Server
        mcp.run()

except ImportError:
    # MCP SDK 未安装时的降级处理
    logger.warning("MCP SDK 未安装，MCP Server 不可用。运行 'pip install mcp' 安装。")

    if __name__ == "__main__":
        print("MCP SDK 未安装，请运行：pip install mcp")
