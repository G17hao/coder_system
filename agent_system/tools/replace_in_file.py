"""文件局部替换工具 — 替换文件中指定文本段"""

from __future__ import annotations

from pathlib import Path


def replace_in_file_tool(
    path: str,
    old_text: str,
    new_text: str,
) -> str:
    """替换文件中的指定文本段

    Args:
        path: 文件绝对路径
        old_text: 要被替换的原始文本（必须精确匹配）
        new_text: 替换后的新文本

    Returns:
        操作结果描述字符串
    """
    p = Path(path)
    if not p.exists():
        return f"错误: 文件不存在: {path}"

    content = p.read_text(encoding="utf-8", errors="replace")

    count = content.count(old_text)
    if count == 0:
        return f"错误: 未找到匹配文本。请确认 old_text 完全匹配文件中的内容（含缩进和换行）"
    if count > 1:
        return f"错误: 找到 {count} 处匹配，请提供更精确的上下文以唯一定位"

    new_content = content.replace(old_text, new_text, 1)
    p.write_text(new_content, encoding="utf-8")

    old_lines = old_text.count("\n") + 1
    new_lines = new_text.count("\n") + 1
    return f"成功: 替换了 {old_lines} 行为 {new_lines} 行 ({path})"


# LLM tool_use 工具定义
REPLACE_IN_FILE_TOOL_DEFINITION = {
    "name": "replace_in_file",
    "description": (
        "替换文件中的指定文本段。old_text 必须精确匹配文件中的一段内容（含缩进和换行），"
        "然后将其替换为 new_text。比 write_file 更安全高效——只改需要改的部分，不需要输出整个文件。"
        "建议包含 3-5 行上下文以确保唯一匹配。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件绝对路径",
            },
            "old_text": {
                "type": "string",
                "description": "要被替换的原始文本（必须精确匹配，含缩进和换行）",
            },
            "new_text": {
                "type": "string",
                "description": "替换后的新文本",
            },
        },
        "required": ["path", "old_text", "new_text"],
    },
}
