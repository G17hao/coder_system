"""MCP 能力配置模型"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MCPServerRef:
    """MCP Server 引用"""
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    transport: str = "stdio"
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "transport": self.transport,
            "url": self.url,
            "env": self.env,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MCPServerRef:
        return cls(
            name=data.get("name", ""),
            command=data.get("command", ""),
            args=data.get("args", []),
            transport=data.get("transport", "stdio"),
            url=data.get("url", ""),
            env=data.get("env", {}),
            description=data.get("description", ""),
        )


@dataclass
class MCPCapabilityConfig:
    """任务 MCP 能力配置

    用于在任务级别声明需要使用的 MCP Server 和工具。
    Planner 在规划任务时自动思考和配置。
    """
    enabled: bool = False  # 是否启用 MCP
    required_servers: list[MCPServerRef] = field(default_factory=list)  # 需要的 Server
    required_tools: list[str] = field(default_factory=list)  # 需要的工具名称
    optional_tools: list[str] = field(default_factory=list)  # 可选工具名称
    reasoning: str = ""  # Planner 的思考过程（为什么需要这些 MCP 能力）

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "required_servers": [s.to_dict() for s in self.required_servers],
            "required_tools": self.required_tools,
            "optional_tools": self.optional_tools,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MCPCapabilityConfig:
        servers = [
            MCPServerRef.from_dict(s)
            for s in data.get("required_servers", [])
        ]
        return cls(
            enabled=data.get("enabled", False),
            required_servers=servers,
            required_tools=data.get("required_tools", []),
            optional_tools=data.get("optional_tools", []),
            reasoning=data.get("reasoning", ""),
        )
