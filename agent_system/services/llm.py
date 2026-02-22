"""Anthropic API 封装 — 含 token 计数、超时、重试、流式输出"""

from __future__ import annotations

import json
import logging
import re
import socket
import sys
import time
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
    total_calls: int = 0

    @property
    def total(self) -> int:
        return self.total_input + self.total_output


# 匹配 <think>...</think> 标签（含跨行），用于过滤模型思考内容
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.DOTALL)
# 匹配未闭合的 <think>... 片段（流式场景中最后一块可能未关闭）
_THINK_OPEN_RE = re.compile(r"<think>[\s\S]*$", re.DOTALL)


def _strip_think_tags(text: str | None) -> str:
    """移除 <think>...</think> 标签及其内容"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _THINK_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def _is_retryable_timeout_error(error: Exception) -> bool:
    """判断是否为可重试的底层超时异常"""
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    message = str(error).lower()
    return "timed out" in message or "timeout" in message


def _extract_api_status_error_detail(error: anthropic.APIStatusError) -> str:
    """提取 APIStatusError 可读详情（request_id/响应体）"""
    parts: list[str] = []

    request_id = getattr(error, "request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")

    body = getattr(error, "body", None)
    if body:
        parts.append(f"body={body}")

    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            header_request_id = headers.get("request-id") or headers.get("x-request-id")
            if header_request_id and not request_id:
                parts.append(f"request_id={header_request_id}")

        response_text = getattr(response, "text", None)
        if response_text:
            parts.append(f"response={response_text}")
        else:
            try:
                response_json = response.json()
                parts.append(f"response={response_json}")
            except Exception:
                pass

    if not parts:
        parts.append(str(error))

    detail = " | ".join(parts)
    max_len = 800
    if len(detail) > max_len:
        return detail[:max_len] + "..."
    return detail


def _estimate_request_payload(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """估算请求体规模，用于排查请求过大问题"""
    system_chars = len(system_prompt)
    message_count = len(messages)
    message_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
    tool_count = len(tools) if tools else 0
    tool_schema_chars = len(json.dumps(tools or [], ensure_ascii=False))

    payload_obj = {
        "model": "",
        "max_tokens": 0,
        "temperature": 0,
        "system": system_prompt,
        "messages": messages,
        "tools": tools or [],
    }
    payload_bytes = len(json.dumps(payload_obj, ensure_ascii=False).encode("utf-8"))

    return {
        "system_chars": system_chars,
        "message_count": message_count,
        "message_chars": message_chars,
        "tool_count": tool_count,
        "tool_schema_chars": tool_schema_chars,
        "payload_bytes": payload_bytes,
    }


class LLMService:
    """Anthropic Claude API 封装"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        base_url: str = "",
        timeout: float = 300.0,
        max_retries: int = 4,
    ) -> None:
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout,
            "max_retries": 0,  # SDK 层不重试，由 _call_with_retry 管理重试和日志
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._usage = TokenUsage()
        self._timeout = timeout
        # 保留该字段仅用于兼容历史配置；_call_with_retry 已采用无限重试策略。
        self._max_retries = max_retries

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    def call(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        conversation_log: ConversationLog | None = None,
        label: str = "",
    ) -> LLMResponse:
        """调用 Claude API

        Args:
            system_prompt: 系统提示词
            messages: 消息列表 [{"role": "user", "content": "..."}]
            tools: 工具定义列表（可选）
            conversation_log: 可选的对话日志记录器
            label: 调用标签，用于日志标识（如 "Analyst/T0.1"）

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

        # 请求前日志：帮助定位请求体过大/参数异常问题
        payload = _estimate_request_payload(system_prompt, messages, tools)
        tag = f"[{label}]" if label else "[LLM]"
        logger.info(
            f"    {tag} 请求体 | msgs={payload['message_count']} | "
            f"msg_chars={payload['message_chars']} | system_chars={payload['system_chars']} | "
            f"tools={payload['tool_count']} | tool_chars={payload['tool_schema_chars']} | "
            f"payload≈{payload['payload_bytes']}B"
        )

        # 带重试的 API 调用
        response = self._call_with_retry(label=label, **kwargs)

        # 提取文本内容（过滤 <think> 标签）
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                cleaned = _strip_think_tags(block.text)
                if cleaned:
                    text_parts.append(cleaned)
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

        logger.info(
            f"    {tag} 用量 | +{input_tokens} in / +{output_tokens} out | "
            f"累计 calls={self._usage.total_calls}, in={self._usage.total_input}, out={self._usage.total_output}"
        )

        return result

    def _call_with_retry(self, label: str = "", **kwargs: Any) -> Any:
        """带重试和进度日志的流式 API 调用

        使用 streaming 模式，超时计时器会在每次收到数据时重置，
        只有服务器完全停止发送超过 timeout 秒才会超时。
        超时或临时错误时自动重试。

        Args:
            label: 日志标签（如 "Analyst/T0.1"）

        Returns:
            Anthropic API 响应对象 (Message)

        Raises:
            anthropic.APIError: 不可重试的 API 错误
        """
        tag = f"[{label}]" if label else "[LLM]"
        attempt = 1
        retry_wait_seconds = 10
        max_retry_wait_seconds = 120

        while True:
            try:
                start = time.time()
                if attempt > 1:
                    logger.info(f"    {tag} 重试 (第 {attempt} 次)...")

                # 显示等待提示
                sys.stdout.write(f"\n    {tag} ⏳ 等待响应...")
                sys.stdout.flush()

                # 使用 streaming — 实时逐字输出 LLM 回复到控制台
                with self._client.messages.stream(**kwargs) as stream:
                    streamed_text = False
                    for event in stream:
                        if hasattr(event, "type"):
                            if event.type == "content_block_delta":
                                delta = event.delta
                                if hasattr(delta, "text") and delta.text:
                                    if not streamed_text:
                                        # 用 \r 覆盖等待提示
                                        sys.stdout.write(f"\r    {tag} ")
                                        streamed_text = True
                                    sys.stdout.write(delta.text)
                                    sys.stdout.flush()
                    if streamed_text:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                    elif not streamed_text:
                        # 纯工具调用无文本输出时清除等待提示
                        sys.stdout.write("\r" + " " * 60 + "\r")
                        sys.stdout.flush()
                    response = stream.get_final_message()

                elapsed = time.time() - start
                self._usage.total_calls += 1
                logger.debug(f"    {tag} 响应 {elapsed:.1f}s (累计 {self._usage.total_calls} 次)")
                return response

            except anthropic.APITimeoutError as e:
                elapsed = time.time() - start
                logger.warning(f"    {tag} 超时 ({elapsed:.0f}s) [第 {attempt} 次]")
                logger.warning(f"    {tag} {retry_wait_seconds}s 后重试（退避上限 {max_retry_wait_seconds}s）")
                time.sleep(retry_wait_seconds)
                retry_wait_seconds = min(retry_wait_seconds * 2, max_retry_wait_seconds)
                attempt += 1
                continue

            except anthropic.APIConnectionError as e:
                logger.warning(f"    {tag} 连接错误 [第 {attempt} 次]: {e}")
                logger.warning(f"    {tag} {retry_wait_seconds}s 后重试（退避上限 {max_retry_wait_seconds}s）")
                time.sleep(retry_wait_seconds)
                retry_wait_seconds = min(retry_wait_seconds * 2, max_retry_wait_seconds)
                attempt += 1
                continue

            except anthropic.RateLimitError as e:
                logger.warning(f"    {tag} 速率限制 [第 {attempt} 次]: {e}")
                logger.warning(f"    {tag} {retry_wait_seconds}s 后重试（退避上限 {max_retry_wait_seconds}s）")
                time.sleep(retry_wait_seconds)
                retry_wait_seconds = min(retry_wait_seconds * 2, max_retry_wait_seconds)
                attempt += 1
                continue

            except anthropic.APIStatusError as e:
                # 5xx 服务端错误可重试，4xx 直接抛出
                if e.status_code >= 500:
                    logger.warning(f"    {tag} 服务端 {e.status_code} [第 {attempt} 次]")
                    logger.warning(f"    {tag} 500详情: {_extract_api_status_error_detail(e)}")
                    logger.warning(f"    {tag} {retry_wait_seconds}s 后重试（退避上限 {max_retry_wait_seconds}s）")
                    time.sleep(retry_wait_seconds)
                    retry_wait_seconds = min(retry_wait_seconds * 2, max_retry_wait_seconds)
                    attempt += 1
                    continue
                else:
                    raise

            except Exception as e:
                if _is_retryable_timeout_error(e):
                    logger.warning(f"    {tag} 底层超时 [第 {attempt} 次]: {e}")
                    logger.warning(f"    {tag} {retry_wait_seconds}s 后重试（退避上限 {max_retry_wait_seconds}s）")
                    time.sleep(retry_wait_seconds)
                    retry_wait_seconds = min(retry_wait_seconds * 2, max_retry_wait_seconds)
                    attempt += 1
                    continue
                raise

    def call_with_tools_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_executor: Any,
        max_iterations: int = 300,
        soft_limit: int = 30,
        conversation_log: ConversationLog | None = None,
        label: str = "",
    ) -> LLMResponse:
        """带工具调用循环的 LLM 调用

        持续调用直到 LLM 不再发起 tool_use 或达到硬上限。
        达到软限制时，注入反思提示让 LLM 自评进度，
        若 LLM 认为应继续则放行，否则中断。

        Args:
            system_prompt: 系统提示词
            messages: 初始消息列表
            tools: 工具定义
            tool_executor: 工具执行器，需要有 execute(name, input) -> str 方法
            max_iterations: 硬上限迭代次数（默认 300）
            soft_limit: 软限制轮次，达到时触发反思检查（默认 30）
            conversation_log: 可选的对话日志记录器

        Returns:
            最终的 LLMResponse
        """
        current_messages = list(messages)
        final_response: LLMResponse | None = None
        last_real_response: LLMResponse | None = None  # 最后一次真实 tool-loop 响应
        reflection_done = False
        tag = f"[{label}]" if label else "[LLM]"

        logger.info(f"    {tag} 开始工具循环 (上限 {max_iterations} 轮, 软限制 {soft_limit} 轮)")

        # 初始化对话日志
        if conversation_log is not None:
            conversation_log.add_system(system_prompt)
            for msg in messages:
                if msg.get("role") == "user":
                    conversation_log.add_user(msg.get("content", ""))

        for iteration in range(max_iterations):
            # 软限制反思检查：达到 soft_limit 时注入反思提示
            if iteration == soft_limit and not reflection_done:
                reflection_done = True
                logger.warning(f"    {tag} 已达软限制 ({soft_limit} 轮)，注入反思检查...")
                reflection_prompt = (
                    f"[系统提醒] 你已经进行了 {soft_limit} 轮工具调用。请简短回答：\n"
                    f"1. 当前任务进展如何（已完成哪些文件/步骤）？\n"
                    f"2. 是否存在无效循环（反复读同一文件、重复失败的操作）？\n"
                    f"3. 剩余工作是否可以在合理轮次内完成？\n\n"
                    f"**重要：只回复以下两种之一，不要输出文件内容或 JSON：**\n"
                    f"- 回复 'CONTINUE: <一句话说明剩余计划>' — 任务正在推进，需要继续使用工具\n"
                    f"- 回复 'DONE: <一句话说明完成情况>' — 所有文件已通过工具写入磁盘，无需再调用工具"
                )
                current_messages.append({"role": "user", "content": reflection_prompt})
                if conversation_log is not None:
                    conversation_log.add_user(reflection_prompt)

                # 调用 LLM 获取反思结果（不提供工具，强制纯文本回复）
                reflection_response = self.call(
                    system_prompt, current_messages, tools=None,
                    conversation_log=conversation_log,
                    label=f"{label}/反思" if label else "反思",
                )
                logger.info(f"    {tag} 反思结果: {reflection_response.content[:200]}")

                # 将反思回复加入消息历史
                current_messages.append({
                    "role": "assistant",
                    "content": reflection_response.content,
                })

                if "CONTINUE" in reflection_response.content.upper():
                    logger.info(f"    {tag} LLM 确认继续，放行至硬上限")
                    continue
                else:
                    logger.info(f"    {tag} LLM 完成 (DONE)，退出工具循环")
                    # 使用反思前最后一次真实 tool-loop 响应，确保 from_json 有可用内容
                    if last_real_response is not None:
                        final_response = last_real_response
                    else:
                        final_response = reflection_response
                    break

            call_start = time.time()
            response = self.call(
                system_prompt, current_messages, tools,
                conversation_log=conversation_log,
                label=label,
            )
            call_elapsed = time.time() - call_start
            logger.info(
                f"    {tag} 轮 {iteration + 1} | "
                f"tools={len(response.tool_calls)} | "
                f"+{response.input_tokens}in/+{response.output_tokens}out | "
                f"{call_elapsed:.1f}s"
            )
            final_response = response
            last_real_response = response  # 记录最后一次真实 tool-loop 响应

            if not response.tool_calls:
                logger.info(f"    {tag} 完成 (共 {iteration + 1} 轮)")
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
                tool_input_summary = str(tc["input"])[:120]
                logger.info(f"    {tag} 🔧 {tool_name}({tool_input_summary})")
                result = tool_executor.execute(tc["name"], tc["input"])
                result_str = str(result)
                logger.debug(f"    {tag} 🔧 {tool_name} -> {result_str[:300]}")
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

        else:
            # for 循环正常结束 = 达到硬上限
            logger.error(f"    {tag} 达到硬上限 ({max_iterations} 轮)，强制中断！")

        logger.info(
            f"    {tag} 循环结束 | 累计 {self._usage.total_input}in/{self._usage.total_output}out"
        )

        assert final_response is not None
        return final_response
