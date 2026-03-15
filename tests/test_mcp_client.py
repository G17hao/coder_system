"""MCP 客户端兼容行为测试"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_orchestrator_uses_project_default_mcp_servers_when_task_has_no_selection() -> None:
    """任务未显式配置 MCP 时，应复用项目级默认 MCP 配置。"""
    from agent_system.models.context import AgentContext
    from agent_system.models.mcp_config import MCPServerRef
    from agent_system.models.project_config import ProjectConfig
    from agent_system.models.task import Task
    from agent_system.orchestrator import Orchestrator

    project = ProjectConfig(
        project_name="demo",
        project_description="demo",
        project_root="E:/demo",
        reference_roots=[],
        git_branch="feat/demo",
        coding_conventions="",
        review_checklist=[],
        review_commands=[],
        task_categories=[],
        initial_tasks=[],
        mcp_default_enabled=True,
        mcp_servers=[
            MCPServerRef(
                name="cocos-creator",
                transport="streamable-http",
                url="http://127.0.0.1:23000/mcp",
            )
        ],
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator._context = AgentContext(project=project)

    enabled, servers = orchestrator._resolve_mcp_runtime_config(
        Task(id="T1", title="demo", description="demo")
    )

    assert enabled is True
    assert len(servers) == 1
    assert servers[0].name == "cocos-creator"
    assert servers[0].url == "http://127.0.0.1:23000/mcp"


def test_mcp_client_aliases_duplicate_tool_names_across_servers() -> None:
    """不同 Server 出现同名工具时，应为后续工具分配别名。"""
    from agent_system.services.mcp_client import MCPClient

    class _FakeSession:
        def __init__(self, tools: list[dict[str, object]]) -> None:
            self._tools = tools

        async def list_tools(self) -> object:
            return SimpleNamespace(tools=self._tools)

    async def _run() -> MCPClient:
        client = MCPClient()
        client._sessions["server-a"] = _FakeSession([
            {"name": "inspect_node", "description": "A", "inputSchema": {}},
        ])
        client._sessions["server-b"] = _FakeSession([
            {"name": "inspect_node", "description": "B", "inputSchema": {}},
        ])

        await client._discover_tools("server-a")
        await client._discover_tools("server-b")
        return client

    client = asyncio.run(_run())

    assert "inspect_node" in client.get_tool_names()
    assert "server-b__inspect_node" in client.get_tool_names()


def test_fetch_mcp_http_capabilities_ignores_unsupported_resources_and_prompts(monkeypatch) -> None:
    """HTTP 降级探测时，未实现的 resources/prompts 应回退为空列表。"""
    from agent_system.services import mcp_client as mcp_module

    async def _fake_initialize(self) -> dict[str, object]:
        return {}

    async def _fake_list_tools(self) -> dict[str, object]:
        return {"tools": [{"name": "scene_open_scene", "description": "open", "inputSchema": {}}]}

    async def _fake_list_resources(self) -> dict[str, object]:
        raise mcp_module.MCPMethodNotSupportedError("Unknown method: resources/list")

    async def _fake_list_prompts(self) -> dict[str, object]:
        raise mcp_module.MCPMethodNotSupportedError("Unknown method: prompts/list")

    monkeypatch.setattr(mcp_module.MCPRawHttpClient, "initialize", _fake_initialize)
    monkeypatch.setattr(mcp_module.MCPRawHttpClient, "list_tools", _fake_list_tools)
    monkeypatch.setattr(mcp_module.MCPRawHttpClient, "list_resources", _fake_list_resources)
    monkeypatch.setattr(mcp_module.MCPRawHttpClient, "list_prompts", _fake_list_prompts)

    capabilities = asyncio.run(
        mcp_module.fetch_mcp_http_capabilities("http://127.0.0.1:23000/mcp")
    )

    assert len(capabilities["tools"]) == 1
    assert capabilities["resources"] == []
    assert capabilities["prompts"] == []