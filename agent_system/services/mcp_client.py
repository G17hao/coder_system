"""MCP 客户端服务 — 调用外部 MCP Server 提供的工具"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    name: str
    command: str = ""  # 启动命令，如 "python" 或 "npx"
    args: list[str] = field(default_factory=list)  # 命令参数，如 ["server.py"]
    transport: str = "stdio"  # 传输类型：stdio / streamable-http / http
    url: str = ""  # HTTP MCP Server 地址
    env: dict[str, str] = field(default_factory=dict)  # 环境变量
    timeout: float = 30.0  # 工具调用超时


@dataclass
class MCPToolDefinition:
    """MCP 工具定义"""
    name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str  # 所属 Server


@dataclass
class MCPToolResult:
    """MCP 工具调用结果"""
    success: bool
    content: str
    error: str | None = None


class MCPProtocolError(RuntimeError):
    """MCP 协议调用异常。"""


class MCPMethodNotSupportedError(MCPProtocolError):
    """MCP 方法不支持。"""


@dataclass
class MCPRawHttpClient:
    """基于原始 JSON-RPC 的 HTTP MCP 降级客户端。"""

    url: str
    timeout: float = 30.0
    _request_id: int = 0

    async def initialize(self) -> dict[str, Any]:
        return await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agent-system",
                    "version": "0.1.0",
                },
            },
        )

    async def list_tools(self) -> dict[str, Any]:
        return await self._request("tools/list", {})

    async def list_resources(self) -> dict[str, Any]:
        return await self._request("resources/list", {})

    async def list_prompts(self) -> dict[str, Any]:
        return await self._request("prompts/list", {})

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

    async def aclose(self) -> None:
        return None

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        response = await asyncio.to_thread(self._post_json, payload)
        error_payload = response.get("error")
        if isinstance(error_payload, dict):
            message = str(error_payload.get("message", "未知错误"))
            if "Unknown method" in message:
                raise MCPMethodNotSupportedError(message)
            raise MCPProtocolError(message)
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError(f"MCP 响应缺少 result: {response}")
        return result

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            raise MCPProtocolError(f"HTTP {exc.code}: {raw_body}") from exc
        except error.URLError as exc:
            raise MCPProtocolError(f"请求 MCP Server 失败: {exc}") from exc

        try:
            decoded = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(f"MCP 响应不是合法 JSON: {raw_body}") from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError(f"MCP 响应格式异常: {decoded}")
        return decoded


async def fetch_mcp_http_capabilities(url: str, timeout: float = 30.0) -> dict[str, list[dict[str, Any]]]:
    """通过 HTTP 获取 MCP tools/resources/prompts，必要时容忍服务端未实现的接口。"""
    client = MCPRawHttpClient(url=url, timeout=timeout)
    await client.initialize()

    tools_result = await client.list_tools()

    try:
        resources_result = await client.list_resources()
        resources = resources_result.get("resources", [])
    except MCPMethodNotSupportedError:
        resources = []

    try:
        prompts_result = await client.list_prompts()
        prompts = prompts_result.get("prompts", [])
    except MCPMethodNotSupportedError:
        prompts = []

    return {
        "tools": _coerce_dict_list(tools_result.get("tools", [])),
        "resources": _coerce_dict_list(resources),
        "prompts": _coerce_dict_list(prompts),
    }


def _coerce_dict_list(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


class MCPClient:
    """MCP 客户端

    职责:
    - 连接到一个或多个 MCP Server
    - 发现 Server 提供的工具
    - 调用远程工具
    - 管理连接生命周期
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._sessions: dict[str, Any] = {}  # Server name -> Session
        self._connections: dict[str, AsyncExitStack] = {}  # Server name -> transport/session cleanup stack
        self._available_tools: dict[str, MCPToolDefinition] = {}  # Exposed tool name -> Definition
        self._initialized = False

    def register_server(self, config: MCPServerConfig) -> None:
        """注册 MCP Server

        Args:
            config: Server 配置
        """
        self._servers[config.name] = config
        if config.transport in {"streamable-http", "http"}:
            logger.info(f"[MCP] 注册 Server: {config.name} ({config.transport} {config.url})")
        else:
            logger.info(f"[MCP] 注册 Server: {config.name} ({config.command} {' '.join(config.args)})")

    async def connect_to_server(self, server_name: str) -> bool:
        """连接到指定的 MCP Server

        Args:
            server_name: Server 名称

        Returns:
            是否连接成功
        """
        if server_name not in self._servers:
            logger.error(f"[MCP] Server 不存在：{server_name}")
            return False

        config = self._servers[server_name]

        try:
            if config.transport in {"streamable-http", "http"}:
                return await self._connect_http_server(server_name, config)

            # 延迟导入 MCP SDK（可选依赖）
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            stack = AsyncExitStack()
            await stack.__aenter__()

            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env={**config.env},
            )

            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(server_params)
            )

            # 创建并初始化 Session
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            self._sessions[server_name] = session
            self._connections[server_name] = stack
            logger.info(f"[MCP] 已连接到 Server: {server_name}")

            # 发现工具
            await self._discover_tools(server_name)

            return True

        except ImportError:
            logger.error("[MCP] 未安装 MCP SDK，请运行：pip install mcp")
            return False
        except Exception as e:
            if 'stack' in locals():
                await stack.aclose()
            logger.error(f"[MCP] 连接 Server 失败：{server_name}: {e}")
            return False

    async def _connect_http_server(self, server_name: str, config: MCPServerConfig) -> bool:
        if not config.url:
            logger.error(f"[MCP] HTTP MCP Server 缺少 url 配置：{server_name}")
            return False

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            stack = AsyncExitStack()
            await stack.__aenter__()
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(config.url)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            self._sessions[server_name] = session
            self._connections[server_name] = stack
            logger.info(f"[MCP] 已通过 SDK 连接到 HTTP Server: {server_name}")

            await self._discover_tools(server_name)
            return True
        except ImportError:
            logger.error("[MCP] 未安装 MCP SDK，请运行：pip install mcp")
            return False
        except Exception as exc:
            if 'stack' in locals():
                await stack.aclose()
            logger.warning(f"[MCP] SDK 连接 HTTP Server 失败，尝试原始 JSON-RPC 降级：{server_name}: {exc}")

        try:
            session = MCPRawHttpClient(url=config.url, timeout=config.timeout)
            await session.initialize()
            self._sessions[server_name] = session
            logger.info(f"[MCP] 已通过原始 JSON-RPC 连接到 HTTP Server: {server_name}")
            await self._discover_tools(server_name)
            return True
        except Exception as exc:
            logger.error(f"[MCP] HTTP MCP Server 降级连接失败：{server_name}: {exc}")
            return False

    async def _discover_tools(self, server_name: str) -> None:
        """发现 Server 提供的工具

        Args:
            server_name: Server 名称
        """
        if server_name not in self._sessions:
            return

        session = self._sessions[server_name]

        try:
            tools = await self._list_tools(session)

            for tool in tools:
                remote_name = str(_extract_attr(tool, "name", ""))
                if not remote_name:
                    continue
                tool_def = MCPToolDefinition(
                    name=self._allocate_tool_name(server_name, remote_name),
                    remote_name=remote_name,
                    description=str(_extract_attr(tool, "description", "") or ""),
                    input_schema=_extract_input_schema(tool),
                    server_name=server_name,
                )
                self._available_tools[tool_def.name] = tool_def
                logger.info(f"[MCP] 发现工具：{tool_def.name} -> {remote_name} (来自 {server_name})")

        except Exception as e:
            logger.warning(f"[MCP] 发现工具失败 ({server_name}): {e}")

    async def _list_tools(self, session: Any) -> list[Any]:
        tools_response = await session.list_tools()
        if isinstance(tools_response, dict):
            return list(tools_response.get("tools", []))
        return list(getattr(tools_response, "tools", []))

    def _allocate_tool_name(self, server_name: str, remote_name: str) -> str:
        existing = self._available_tools.get(remote_name)
        if existing is None:
            return remote_name
        if existing.server_name == server_name and existing.remote_name == remote_name:
            return remote_name

        base_name = f"{server_name}__{remote_name}"
        candidate = base_name
        suffix = 2
        while candidate in self._available_tools:
            candidate = f"{base_name}_{suffix}"
            suffix += 1
        return candidate

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """调用 MCP 工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具调用结果
        """
        if tool_name not in self._available_tools:
            return MCPToolResult(
                success=False,
                content="",
                error=f"未知工具：{tool_name}",
            )

        tool_def = self._available_tools[tool_name]
        server_name = tool_def.server_name

        if server_name not in self._sessions:
            # 尝试自动连接
            connected = await self.connect_to_server(server_name)
            if not connected:
                return MCPToolResult(
                    success=False,
                    content="",
                    error=f"无法连接到 Server: {server_name}",
                )

        session = self._sessions[server_name]

        try:
            result = await session.call_tool(tool_def.remote_name, arguments)
            
            # 提取结果内容
            content_parts = []
            if isinstance(result, dict):
                content = result.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if "text" in item:
                                content_parts.append(str(item["text"]))
                            elif "data" in item:
                                content_parts.append(str(item["data"]))
            elif hasattr(result, 'content') and result.content:
                for item in result.content:
                    if hasattr(item, 'text'):
                        content_parts.append(item.text)
                    elif hasattr(item, 'data'):
                        content_parts.append(str(item.data))

            return MCPToolResult(
                success=True,
                content="\n".join(content_parts) if content_parts else str(result),
                error=None,
            )

        except Exception as e:
            logger.error(f"[MCP] 工具调用失败 ({tool_name}): {e}")
            return MCPToolResult(
                success=False,
                content="",
                error=str(e),
            )

    def get_available_tools(self) -> list[dict[str, Any]]:
        """获取所有可用的 MCP 工具定义（用于 LLM tool_use）

        Returns:
            工具定义列表（Anthropic Messages API 格式）
        """
        tools = []
        for tool_def in self._available_tools.values():
            description = tool_def.description
            if tool_def.name != tool_def.remote_name:
                description = f"{description}\n\n[MCP server: {tool_def.server_name}; remote tool: {tool_def.remote_name}]".strip()
            tools.append({
                "name": tool_def.name,
                "description": description,
                "input_schema": tool_def.input_schema,
            })
        return tools

    def get_tool_names(self) -> list[str]:
        """获取所有可用工具名称

        Returns:
            工具名称列表
        """
        return list(self._available_tools.keys())

    async def disconnect(self, server_name: str | None = None) -> None:
        """断开 MCP Server 连接

        Args:
            server_name: Server 名称（None 表示断开所有）
        """
        if server_name:
            if server_name in self._sessions:
                session = self._sessions[server_name]
                del self._sessions[server_name]
                stack = self._connections.pop(server_name, None)
                if stack is not None:
                    await stack.aclose()
                elif hasattr(session, "aclose"):
                    await session.aclose()
                self._remove_server_tools(server_name)
                logger.info(f"[MCP] 已断开连接：{server_name}")
        else:
            for name in list(self._sessions.keys()):
                session = self._sessions[name]
                stack = self._connections.pop(name, None)
                if stack is not None:
                    await stack.aclose()
                elif hasattr(session, "aclose"):
                    await session.aclose()
                self._remove_server_tools(name)
                logger.info(f"[MCP] 已断开连接：{name}")
            self._sessions.clear()

    def _remove_server_tools(self, server_name: str) -> None:
        for tool_name in [
            name for name, tool_def in self._available_tools.items() if tool_def.server_name == server_name
        ]:
            del self._available_tools[tool_name]

    async def __aenter__(self) -> MCPClient:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器出口"""
        await self.disconnect()


def _extract_attr(tool: Any, name: str, default: Any) -> Any:
    if isinstance(tool, dict):
        return tool.get(name, default)
    return getattr(tool, name, default)


def _extract_input_schema(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        schema = tool.get("inputSchema", {})
    else:
        schema = getattr(tool, "inputSchema", {})
    if isinstance(schema, dict):
        return schema
    return {}
