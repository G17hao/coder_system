"""列出当前 Cocos MCP Server 支持的操作。

默认读取 projects/h5-widget-replication.json 中的 cocos-creator MCP 配置，
连接后输出 tools/resources/prompts 信息。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_system.models.project_config import ProjectConfig
from agent_system.services.mcp_client import fetch_mcp_http_capabilities

if TYPE_CHECKING:
    from agent_system.models.mcp_config import MCPServerRef


@dataclass
class MCPToolSummary:
    """MCP Tool 摘要信息。"""

    name: str
    description: str
    required: list[str]
    properties: list[str]


@dataclass
class MCPResourceSummary:
    """MCP Resource 摘要信息。"""

    name: str
    uri: str
    description: str


@dataclass
class MCPPromptSummary:
    """MCP Prompt 摘要信息。"""

    name: str
    description: str
    arguments: list[str]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="打印当前 Cocos MCP 支持的全部操作（tools/resources/prompts）。"
    )
    parser.add_argument(
        "--project",
        default="projects/h5-widget-replication.json",
        help="项目配置文件路径，默认读取 projects/h5-widget-replication.json",
    )
    parser.add_argument(
        "--server-name",
        default="cocos-creator",
        help="项目配置中的 MCP Server 名称，默认 cocos-creator",
    )
    parser.add_argument(
        "--url",
        default="",
        help="直接指定 MCP URL；提供后将覆盖项目配置中的 URL",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出，便于后续脚本处理",
    )
    return parser


def _find_server(project: ProjectConfig, server_name: str) -> MCPServerRef:
    for server in project.mcp_servers:
        if server.name == server_name:
            return server
    raise ValueError(
        f"项目配置 {project.project_name} 中未找到名为 {server_name!r} 的 MCP Server"
    )


def _extract_required_fields(input_schema: object) -> list[str]:
    if not isinstance(input_schema, dict):
        return []
    required = input_schema.get("required", [])
    if not isinstance(required, list):
        return []
    return [str(item) for item in required]


def _extract_property_fields(input_schema: object) -> list[str]:
    if not isinstance(input_schema, dict):
        return []
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return []
    return [str(name) for name in properties.keys()]


def _read_field(payload: object, name: str, default: object = "") -> object:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


async def _fetch_capabilities(url: str) -> dict[str, list[object]]:
    return await fetch_mcp_http_capabilities(url)


def _summarize_tools(tools: list[object]) -> list[MCPToolSummary]:
    summaries: list[MCPToolSummary] = []
    for tool in tools:
        input_schema = _read_field(tool, "inputSchema", {})
        summaries.append(
            MCPToolSummary(
                name=str(_read_field(tool, "name", "")),
                description=str(_read_field(tool, "description", "") or ""),
                required=_extract_required_fields(input_schema),
                properties=_extract_property_fields(input_schema),
            )
        )
    return summaries


def _summarize_resources(resources: list[object]) -> list[MCPResourceSummary]:
    summaries: list[MCPResourceSummary] = []
    for resource in resources:
        summaries.append(
            MCPResourceSummary(
                name=str(_read_field(resource, "name", "")),
                uri=str(_read_field(resource, "uri", "")),
                description=str(_read_field(resource, "description", "") or ""),
            )
        )
    return summaries


def _summarize_prompts(prompts: list[object]) -> list[MCPPromptSummary]:
    summaries: list[MCPPromptSummary] = []
    for prompt in prompts:
        raw_arguments = _read_field(prompt, "arguments", []) or []
        arguments = [str(_read_field(argument, "name", "")) for argument in raw_arguments]
        summaries.append(
            MCPPromptSummary(
                name=str(_read_field(prompt, "name", "")),
                description=str(_read_field(prompt, "description", "") or ""),
                arguments=arguments,
            )
        )
    return summaries


def _print_text_output(
    url: str,
    tools: list[MCPToolSummary],
    resources: list[MCPResourceSummary],
    prompts: list[MCPPromptSummary],
) -> None:
    print(f"MCP URL: {url}")
    print()
    print(f"=== TOOLS ({len(tools)}) ===")
    for tool in tools:
        print(f"- {tool.name}")
        if tool.description:
            print(f"  描述: {tool.description}")
        if tool.required:
            print(f"  必填参数: {', '.join(tool.required)}")
        elif tool.properties:
            print(f"  可选/可用参数: {', '.join(tool.properties)}")

    print()
    print(f"=== RESOURCES ({len(resources)}) ===")
    for resource in resources:
        print(f"- {resource.name}: {resource.uri}")
        if resource.description:
            print(f"  描述: {resource.description}")

    print()
    print(f"=== PROMPTS ({len(prompts)}) ===")
    for prompt in prompts:
        print(f"- {prompt.name}")
        if prompt.description:
            print(f"  描述: {prompt.description}")
        if prompt.arguments:
            print(f"  参数: {', '.join(prompt.arguments)}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    project_path = Path(args.project)
    project = ProjectConfig.from_file(project_path)
    server = _find_server(project, args.server_name)

    url = args.url or server.url
    if not url:
        raise ValueError(
            f"MCP Server {server.name!r} 未配置 URL，请通过 --url 指定或在项目配置中补充 url"
        )

    capabilities = await _fetch_capabilities(url)
    tools = _summarize_tools(capabilities["tools"])
    resources = _summarize_resources(capabilities["resources"])
    prompts = _summarize_prompts(capabilities["prompts"])

    if args.json:
        payload = {
            "url": url,
            "tools": [asdict(tool) for tool in tools],
            "resources": [asdict(resource) for resource in resources],
            "prompts": [asdict(prompt) for prompt in prompts],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_text_output(url, tools, resources, prompts)

    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())