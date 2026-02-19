"""Anthropic API 封装 — 含 token 计数"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from agent_system.services.conversation_logger import ConversationLog

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 调用结果"""
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


@dataclass
class TokenUsage:
    """Token 使用统计"""
    total_input: int = 0
    total_output: int = 0

    @property
    def total(self) -> int:
        return self.total_input + self.total_output


class LLMService:
    """Anthropic Claude API 封装"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        base_url: str = "",
    ) -> None:
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._usage = TokenUsage()

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    def call(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        conversation_log: ConversationLog | None = None,
    ) -> LLMResponse:
        """调用 Claude API

        Args:
            system_prompt: 系统提示词
            messages: 消息列表 [{"role": "user", "content": "..."}]
            tools: 工具定义列表（可选）
            conversation_log: 可选的对话日志记录器

        Returns:
            LLMResponse 包含内容、工具调用和 token 统计
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        # 提取文本内容
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # 更新 token 统计
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        self._usage.total_input += input_tokens
        self._usage.total_output += output_tokens

        result = LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=response.stop_reason or "",
        )

        # 记录到对话日志
        if conversation_log is not None:
            conversation_log.add_assistant(
                content=result.content,
                tool_calls=result.tool_calls or None,
            )
            conversation_log.add_token_usage(input_tokens, output_tokens)

        return result

    def call_with_tools_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_executor: Any,
        max_iterations: int = 10,
        conversation_log: ConversationLog | None = None,
    ) -> LLMResponse:
        """带工具调用循环的 LLM 调用

        持续调用直到 LLM 不再发起 tool_use 或达到最大迭代次数。

        Args:
            system_prompt: 系统提示词
            messages: 初始消息列表
            tools: 工具定义
            tool_executor: 工具执行器，需要有 execute(name, input) -> str 方法
            max_iterations: 最大迭代次数
            conversation_log: 可选的对话日志记录器

        Returns:
            最终的 LLMResponse
        """
        current_messages = list(messages)
        final_response: LLMResponse | None = None

        # 初始化对话日志
        if conversation_log is not None:
            conversation_log.add_system(system_prompt)
            for msg in messages:
                if msg.get("role") == "user":
                    conversation_log.add_user(msg.get("content", ""))

        for iteration in range(max_iterations):
            logger.info(f"    [LLM] 第 {iteration + 1}/{max_iterations} 轮对话...")
            response = self.call(
                system_prompt, current_messages, tools,
                conversation_log=conversation_log,
            )
            logger.info(
                f"    [LLM] 响应: stop={response.stop_reason}, "
                f"tools={len(response.tool_calls)}, "
                f"tokens=+{response.input_tokens}in/+{response.output_tokens}out"
            )
            final_response = response

            if not response.tool_calls:
                logger.info(f"    [LLM] 对话结束 (无工具调用)")
                break

            # 构建 assistant 消息（包含 tool_use blocks）
            assistant_content: list[dict[str, Any]] = []
            if response.content:
                assistant_content.append({"type": "text", "text": response.content})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            current_messages.append({"role": "assistant", "content": assistant_content})

            # 执行工具并构建 tool_result 消息
            tool_results: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_input_summary = str(tc["input"])[:200]
                logger.info(f"    [tool] {tool_name}({tool_input_summary})")
                result = tool_executor.execute(tc["name"], tc["input"])
                result_str = str(result)
                logger.debug(f"    [tool] {tool_name} -> {result_str[:300]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                })
                # 记录工具结果
                if conversation_log is not None:
                    conversation_log.add_tool_result(
                        tool_use_id=tc["id"],
                        tool_name=tc["name"],
                        result=result_str,
                    )
            current_messages.append({"role": "user", "content": tool_results})

        logger.info(
            f"    [LLM] 工具循环结束, 累计 tokens: {self._usage.total_input}in/{self._usage.total_output}out"
        )

        assert final_response is not None
        return final_response
