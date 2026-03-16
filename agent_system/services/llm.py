"""Anthropic API 封装 — 含 token 计数、超时、重试、流式输出"""

from __future__ import annotations

import json
import logging
import re
import socket
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from agent_system.services.conversation_logger import ConversationLog

logger = logging.getLogger(__name__)

_MAX_REQUEST_BYTES = 5_500_000
_MIN_MESSAGES_TO_KEEP = 6
# 触发滚动摘要的请求体大小阈值，超过后优先尝试压缩历史上下文。
_DEFAULT_SUMMARY_TRIGGER_BYTES = 4_200_000
# 执行滚动摘要后仍然原样保留的最近消息条数。
_DEFAULT_SUMMARY_KEEP_RECENT_MESSAGES = 8
# 将摘要同步回对话日志时，额外保留的最近日志条数。
_DEFAULT_SUMMARY_KEEP_RECENT_LOG_ENTRIES = 8
_SUMMARY_BLOCK_START = "\n\n[CONTEXT SUMMARY START]\n"
_SUMMARY_BLOCK_END = "\n[CONTEXT SUMMARY END]\n"


@dataclass
class LLMResponse:
    """LLM 调用结果"""
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    cached_tokens: int = 0  # 命中的缓存 token 数
    cache_creation_tokens: int = 0  # 创建的缓存 token 数


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
_INPUT_LENGTH_LIMIT_RE = re.compile(r"Range of input length should be \[\d+,\s*(\d+)\]")


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


def _extract_input_length_limit(error: Exception) -> int | None:
    """从供应商报错中提取允许的最大输入长度。"""
    match = _INPUT_LENGTH_LIMIT_RE.search(str(error))
    if not match:
        return None

    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


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


def _truncate_middle(text: str, max_chars: int) -> str:
    """保留首尾信息的中间截断。"""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 32:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - 19
    return text[:head] + "\n...[已截断]...\n" + text[-max(0, tail):]


def _shrink_message_content(content: Any, tool_result_chars: int, text_chars: int) -> Any:
    """压缩单条消息内容，优先裁剪冗长工具结果。"""
    if isinstance(content, str):
        return _truncate_middle(content, text_chars)

    if not isinstance(content, list):
        return content

    shrunk_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            shrunk_blocks.append(block)
            continue

        block_type = block.get("type")
        new_block = dict(block)
        if block_type == "tool_result":
            block_content = block.get("content", "")
            if isinstance(block_content, str):
                new_block["content"] = _truncate_middle(block_content, tool_result_chars)
        elif block_type == "text":
            block_text = block.get("text", "")
            if isinstance(block_text, str):
                new_block["text"] = _truncate_middle(block_text, text_chars)
        shrunk_blocks.append(new_block)
    return shrunk_blocks


def _fit_messages_to_payload(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_bytes: int = _MAX_REQUEST_BYTES,
) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    """在请求发送前裁剪消息，避免超过服务端请求体大小限制。"""
    payload = _estimate_request_payload(system_prompt, messages, tools)
    if payload["payload_bytes"] <= max_bytes:
        return (messages, payload, False)

    working = deepcopy(messages)
    trim_stages = [
        (8, 12_000, 8_000),
        (6, 6_000, 4_000),
        (4, 2_000, 1_500),
        (2, 600, 600),
    ]

    for keep_recent, tool_chars, text_chars in trim_stages:
        cutoff = max(0, len(working) - keep_recent)
        for index in range(cutoff):
            content = working[index].get("content")
            working[index]["content"] = _shrink_message_content(content, tool_chars, text_chars)

        payload = _estimate_request_payload(system_prompt, working, tools)
        if payload["payload_bytes"] <= max_bytes:
            return (working, payload, True)

    while len(working) > _MIN_MESSAGES_TO_KEEP and payload["payload_bytes"] > max_bytes:
        del working[0]
        payload = _estimate_request_payload(system_prompt, working, tools)

    if payload["payload_bytes"] > max_bytes:
        for index in range(len(working)):
            content = working[index].get("content")
            working[index]["content"] = _shrink_message_content(content, 200, 200)
        payload = _estimate_request_payload(system_prompt, working, tools)

    return (working, payload, True)


def _render_content_for_summary(content: Any, max_chars: int = 1200) -> str:
    """将消息内容转换为适合摘要模型消费的纯文本。"""
    if isinstance(content, str):
        return _truncate_middle(content, max_chars)

    if not isinstance(content, list):
        return _truncate_middle(str(content), max_chars)

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(_truncate_middle(str(block), max_chars // 2))
            continue

        block_type = block.get("type")
        if block_type == "text":
            parts.append(f"文本: {_truncate_middle(str(block.get('text', '')), max_chars // 2)}")
        elif block_type == "tool_use":
            tool_name = str(block.get("name", "unknown"))
            tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
            parts.append(f"工具调用 {tool_name}: {_truncate_middle(tool_input, max_chars // 2)}")
        elif block_type == "tool_result":
            tool_use_id = str(block.get("tool_use_id", "unknown"))
            result = block.get("content", "")
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            parts.append(f"工具结果 {tool_use_id}: {_truncate_middle(result, max_chars // 2)}")
        else:
            parts.append(_truncate_middle(json.dumps(block, ensure_ascii=False), max_chars // 2))

    return "\n".join(part for part in parts if part)


def _render_message_for_summary(message: dict[str, Any], index: int) -> str:
    """渲染单条历史消息，供摘要模型使用。"""
    role = str(message.get("role", "unknown"))
    role_label = {
        "user": "用户",
        "assistant": "助手",
        "tool": "工具",
    }.get(role, role)
    content_text = _render_content_for_summary(message.get("content", ""))
    return f"### {role_label} 消息 {index}\n{content_text}"


def _extract_summary_from_system_prompt(system_prompt: str) -> str:
    """提取已嵌入 system prompt 的滚动摘要。"""
    start = system_prompt.find(_SUMMARY_BLOCK_START)
    if start == -1:
        return ""

    end = system_prompt.find(_SUMMARY_BLOCK_END, start)
    if end == -1:
        return ""

    summary_block = system_prompt[start + len(_SUMMARY_BLOCK_START):end]
    first_newline = summary_block.find("\n")
    if first_newline == -1:
        return summary_block.strip()
    return summary_block[first_newline + 1:].strip()


def _get_summary_trigger_reason(
    messages: list[dict[str, Any]],
    payload: dict[str, int],
    summary_trigger_bytes: int,
    summary_keep_recent_messages: int,
) -> str | None:
    """返回触发滚动摘要的原因；未触发时返回 None。"""
    if len(messages) <= summary_keep_recent_messages:
        return None

    if payload["payload_bytes"] < summary_trigger_bytes:
        return None

    return (
        f"请求体达到摘要阈值：payload≈{payload['payload_bytes']}B，"
        f"trigger={summary_trigger_bytes}B，"
        f"可压缩历史消息={len(messages) - summary_keep_recent_messages} 条"
    )


def _merge_summary_into_system_prompt(system_prompt: str, summary: str, compressed_count: int) -> str:
    """将滚动摘要嵌入 system prompt，并替换旧摘要块。"""
    summary_block = (
        f"{_SUMMARY_BLOCK_START}"
        f"以下摘要概括了更早的 {compressed_count} 条历史消息；若与后续更近的消息或工具结果冲突，以较新的上下文为准。\n"
        f"{summary.strip()}\n"
        f"{_SUMMARY_BLOCK_END}"
    )

    start = system_prompt.find(_SUMMARY_BLOCK_START)
    if start == -1:
        return system_prompt.rstrip() + summary_block

    end = system_prompt.find(_SUMMARY_BLOCK_END, start)
    if end == -1:
        return system_prompt.rstrip() + summary_block

    end += len(_SUMMARY_BLOCK_END)
    return system_prompt[:start] + summary_block + system_prompt[end:]


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
        summary_trigger_bytes: int = _DEFAULT_SUMMARY_TRIGGER_BYTES,
        summary_keep_recent_messages: int = _DEFAULT_SUMMARY_KEEP_RECENT_MESSAGES,
        summary_keep_recent_log_entries: int = _DEFAULT_SUMMARY_KEEP_RECENT_LOG_ENTRIES,
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
        # MCP 额外工具
        self._extra_tools: list[dict[str, Any]] = []
        self._summary_trigger_bytes = summary_trigger_bytes
        self._summary_keep_recent_messages = summary_keep_recent_messages
        self._summary_keep_recent_log_entries = summary_keep_recent_log_entries

    @property
    def usage(self) -> TokenUsage:
        return self._usage


    def register_extra_tools(self, tools: list[dict[str, Any]]) -> None:
        """注册额外工具（如 MCP 工具）

        Args:
            tools: 工具定义列表
        """
        self._extra_tools.extend(tools)
        logger.info(f"[LLM] 注册 {len(tools)} 个额外工具，累计 {len(self._extra_tools)} 个")

    def get_all_tools(self, base_tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """获取所有可用工具（基础工具 + MCP 工具）

        Args:
            base_tools: 基础工具列表（可选）

        Returns:
            合并后的工具列表
        """
        if base_tools is None:
            return list(self._extra_tools)
        return list(base_tools) + list(self._extra_tools)

    def call(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        conversation_log: ConversationLog | None = None,
        label: str = "",
        enable_cache: bool = True,
    ) -> LLMResponse:
        """调用 Claude API（支持 DashScope 显式缓存）

        Args:
            system_prompt: 系统提示词
            messages: 消息列表 [{"role": "user", "content": "..."}]
            tools: 工具定义列表（可选）
            conversation_log: 可选的对话日志记录器
            label: 调用标签，用于日志标识（如 "Analyst/T0.1"）
            enable_cache: 是否启用显式缓存（仅对 DashScope/阿里百炼有效）

        Returns:
            LLMResponse 包含内容、工具调用和 token 统计
        """
        system_tokens = len(system_prompt) // 4  # 估算 token 数
        use_cache = enable_cache and system_tokens >= getattr(self, '_cache_min_tokens', 1024)
        tag = f"[{label}]" if label else "[LLM]"

        def _prepare_request(max_bytes: int) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any], bool]:
            fitted_messages, payload, was_trimmed = _fit_messages_to_payload(
                system_prompt,
                messages,
                tools,
                max_bytes=max_bytes,
            )
            if was_trimmed:
                logger.warning(
                    f"    {tag} 请求体过大，已自动裁剪上下文 | payload≈{payload['payload_bytes']}B | limit={max_bytes}B"
                )

            if use_cache:
                request_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                    "system": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": fitted_messages,
                }
            else:
                request_kwargs = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                    "system": system_prompt,
                    "messages": fitted_messages,
                }

            if tools:
                request_kwargs["tools"] = tools

            return fitted_messages, payload, request_kwargs, was_trimmed

        request_max_bytes = max(1024, int(getattr(self, '_request_max_bytes', _MAX_REQUEST_BYTES)))
        fitted_messages, payload, kwargs, _ = _prepare_request(request_max_bytes)

        if use_cache:
            logger.info(f"    {tag} 启用显式缓存 (system_prompt ≈ {system_tokens} tokens)")

        logger.info(
            f"    {tag} 请求体 | msgs={payload['message_count']} | "
            f"msg_chars={payload['message_chars']} | system_chars={payload['system_chars']} | "
            f"tools={payload['tool_count']} | tool_chars={payload['tool_schema_chars']} | "
            f"payload≈{payload['payload_bytes']}B"
        )

        try:
            response = self._call_with_retry(label=label, **kwargs)
        except Exception as error:
            provider_limit = _extract_input_length_limit(error)
            if provider_limit is None:
                raise

            fallback_limit = max(1024, min(provider_limit - 4096, int(provider_limit * 0.95)))
            if fallback_limit >= request_max_bytes:
                raise

            logger.warning(
                f"    {tag} 供应商拒绝当前输入长度，自动收紧请求体限制后重试 | "
                f"provider_limit={provider_limit}B | retry_limit={fallback_limit}B"
            )
            self._request_max_bytes = fallback_limit
            fitted_messages, payload, kwargs, _ = _prepare_request(fallback_limit)
            logger.info(
                f"    {tag} 重试请求体 | msgs={payload['message_count']} | "
                f"msg_chars={payload['message_chars']} | system_chars={payload['system_chars']} | "
                f"tools={payload['tool_count']} | tool_chars={payload['tool_schema_chars']} | "
                f"payload≈{payload['payload_bytes']}B"
            )
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

        # 提取缓存统计（DashScope 格式）
        cached_tokens = 0
        cache_creation_tokens = 0
        if hasattr(response.usage, 'prompt_tokens_details'):
            details = response.usage.prompt_tokens_details
            cached_tokens = getattr(details, 'cached_tokens', 0) or 0
            cache_creation_tokens = getattr(details, 'cache_creation_input_tokens', 0) or 0
        
        result = LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=response.stop_reason or "",
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

        # 记录到对话日志
        if conversation_log is not None:
            conversation_log.add_assistant(
                content=result.content,
                tool_calls=result.tool_calls or None,
            )
            conversation_log.add_token_usage(input_tokens, output_tokens)

        # 缓存命中日志
        cache_log = ""
        if cached_tokens > 0:
            cache_log = f" | 缓存命中：{cached_tokens}"
        if cache_creation_tokens > 0:
            cache_log += f" | 缓存创建：{cache_creation_tokens}"
        
        logger.info(
            f"    {tag} 用量 | +{input_tokens} in / +{output_tokens} out{cache_log} | "
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

    def _sync_summary_to_conversation_log(
        self,
        conversation_log: ConversationLog | None,
        summary: str,
    ) -> None:
        """将滚动摘要同步到对话日志，避免日志与真实上下文脱节。"""
        if conversation_log is None:
            return

        from .conversation_logger import ConversationEntry

        def _is_summary_entry(entry: ConversationEntry) -> bool:
            return (
                entry.role == "system"
                and isinstance(entry.content, str)
                and entry.content.startswith("[对话摘要]")
            )

        preserved_entries = [entry for entry in conversation_log.entries if not _is_summary_entry(entry)]
        keep_recent_log_entries = getattr(
            self,
            "_summary_keep_recent_log_entries",
            _DEFAULT_SUMMARY_KEEP_RECENT_LOG_ENTRIES,
        )
        preserved_entries = preserved_entries[-keep_recent_log_entries:]
        summary_entry = ConversationEntry(
            role="system",
            content=f"[对话摘要] 以下为滚动摘要，代表更早的历史上下文：\n\n{summary.strip()}",
        )
        conversation_log.entries = [summary_entry] + preserved_entries

    def _summarize_message_history(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        conversation_log: ConversationLog | None = None,
        label: str = "",
    ) -> tuple[str, list[dict[str, Any]], bool]:
        """将较早消息滚动摘要到 system prompt，真正缩短后续请求上下文。"""
        summary_trigger_bytes = getattr(self, "_summary_trigger_bytes", _DEFAULT_SUMMARY_TRIGGER_BYTES)
        summary_keep_recent_messages = getattr(
            self,
            "_summary_keep_recent_messages",
            _DEFAULT_SUMMARY_KEEP_RECENT_MESSAGES,
        )
        payload = _estimate_request_payload(system_prompt, messages, tools)
        summary_reason = _get_summary_trigger_reason(
            messages,
            payload,
            summary_trigger_bytes,
            summary_keep_recent_messages,
        )
        if summary_reason is None:
            return (system_prompt, messages, False)

        compress_count = len(messages) - summary_keep_recent_messages
        entries_to_compress = messages[:compress_count]
        remaining_messages = messages[compress_count:]
        if not entries_to_compress:
            return (system_prompt, messages, False)

        tag = f"[{label}]" if label else "[LLM]"
        logger.info(
            f"    {tag} 开始滚动摘要：{summary_reason} | "
            f"压缩前 {len(entries_to_compress)} 条消息，保留后 {len(remaining_messages)} 条"
        )

        history_text = "\n\n".join(
            _render_message_for_summary(message, index + 1)
            for index, message in enumerate(entries_to_compress)
        )
        existing_summary = _extract_summary_from_system_prompt(system_prompt)
        existing_summary_section = ""
        if existing_summary:
            existing_summary_section = f"## 既有历史摘要\n{existing_summary[:4000]}\n\n"

        compress_prompt = (
            "你是一个专业的上下文摘要助手。请将既有历史摘要与新加入的早期消息合并为一段新的滚动摘要。\n\n"
            "要求：\n"
            "1. 保留任务目标、已完成工作、关键文件路径、关键决策、错误与限制、未完成事项。\n"
            "2. 保留重要工具调用结论，不保留无价值的逐字日志。\n"
            "3. 如果存在尚未解决的问题，必须明确保留。\n"
            "4. 输出纯文本，不要 JSON，不要 markdown 标题，不要代码块。\n"
            "5. 输出控制在 200-600 字，优先压缩重复信息。\n\n"
            f"{existing_summary_section}"
            f"## 新增待合并的早期消息\n{history_text[:20000]}"
        )

        summary_response = self.call(
            system_prompt=(
                "你是技术任务上下文压缩专家。"
                "输出单段纯文本摘要，必须保留后续工作继续所需的信息。"
            ),
            messages=[{"role": "user", "content": compress_prompt}],
            tools=None,
            conversation_log=None,
            label=f"{label}/摘要" if label else "摘要",
            enable_cache=False,
        )
        summary = summary_response.content.strip()
        if not summary:
            logger.warning(f"    {tag} 滚动摘要为空，跳过本次压缩")
            return (system_prompt, messages, False)

        updated_system_prompt = _merge_summary_into_system_prompt(
            system_prompt,
            summary,
            len(entries_to_compress),
        )
        self._sync_summary_to_conversation_log(conversation_log, summary)

        new_payload = _estimate_request_payload(updated_system_prompt, remaining_messages, tools)
        logger.info(
            f"    {tag} 滚动摘要完成 | msgs {len(messages)} -> {len(remaining_messages)} | "
            f"payload≈{new_payload['payload_bytes']}B"
        )
        return (updated_system_prompt, remaining_messages, True)

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
        active_system_prompt = system_prompt
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
            active_system_prompt, current_messages, _ = self._summarize_message_history(
                system_prompt=active_system_prompt,
                messages=current_messages,
                tools=tools,
                conversation_log=conversation_log,
                label=label,
            )

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
                    active_system_prompt, current_messages, tools=None,
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
                    logger.info(f"    {tag} LLM 完成 (DONE)，进入最终收尾输出")

                    finalize_prompt = (
                        "[系统指令] 不要继续调用任何工具。"
                        "请基于已经完成的检查与工具结果，立即输出该任务要求的最终答案。"
                        "必须严格遵循原始输出格式要求。"
                        "不要输出 CONTINUE/DONE，不要输出过程说明。"
                    )
                    current_messages.append({"role": "user", "content": finalize_prompt})
                    if conversation_log is not None:
                        conversation_log.add_user(finalize_prompt)

                    active_system_prompt, current_messages, _ = self._summarize_message_history(
                        system_prompt=active_system_prompt,
                        messages=current_messages,
                        tools=None,
                        conversation_log=conversation_log,
                        label=label,
                    )

                    finalization_response = self.call(
                        active_system_prompt,
                        current_messages,
                        tools=None,
                        conversation_log=conversation_log,
                        label=f"{label}/收尾" if label else "收尾",
                    )

                    if finalization_response.content.strip():
                        final_response = finalization_response
                    elif last_real_response is not None:
                        final_response = last_real_response
                    else:
                        final_response = reflection_response
                    break

            call_start = time.time()
            response = self.call(
                active_system_prompt, current_messages, tools,
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
    def compress_context(
        self,
        conversation_log: Any,
        label: str = "",
    ) -> bool:
        """使用 LLM 智能压缩对话上下文

        对前 50% 的对话内容进行摘要，保留关键信息和上下文连贯性。

        Args:
            conversation_log: ConversationLog 实例
            label: 日志标签

        Returns:
            是否成功压缩
        """
        from .conversation_logger import ConversationEntry
        
        if conversation_log is None or not conversation_log.entries:
            return False

        tag = f"[{label}]" if label else "[LLM]"
        entries = conversation_log.entries
        original_count = len(entries)

        # 至少需要 6 条记录才进行压缩（前 3 条压缩，后 3 条保留）
        if original_count < 6:
            logger.info(f"    {tag} 对话记录较少 ({original_count} 条)，无需压缩")
            return False

        # 计算要压缩的条目数量（前 50%）
        compress_count = max(1, original_count // 2)
        
        # 确保至少保留 system prompt（如果有）
        start_idx = 0
        if entries[0].role == "system":
            start_idx = 1
            compress_count = max(1, compress_count - 1)
        
        # 确定要压缩的条目范围
        entries_to_compress = entries[start_idx:start_idx + compress_count]
        if not entries_to_compress:
            logger.info(f"    {tag} 无可压缩的条目")
            return False

        # 保留的条目（未被压缩的部分）
        remaining_entries = entries[start_idx + compress_count:]
        
        logger.info(
            f"    {tag} 开始上下文压缩：共 {original_count} 条，"
            f"将压缩前 {len(entries_to_compress)} 条，保留后 {len(remaining_entries)} 条"
        )

        # 构建要压缩的对话内容
        compress_text_parts = []
        for i, entry in enumerate(entries_to_compress):
            role_label = "用户" if entry.role == "user" else "助手"
            if entry.role == "tool_result":
                role_label = "工具结果"
            
            content_str = entry.content
            if isinstance(content_str, dict):
                if entry.role == "tool_result":
                    # 工具结果：提取关键信息
                    tool_name = content_str.get("tool_name", "unknown")
                    result = content_str.get("result", "")[:500]
                    content_str = f"[工具 {tool_name}] 结果：{result[:200]}..."
                else:
                    content_str = json.dumps(content_str, ensure_ascii=False)[:500]
            elif isinstance(content_str, list):
                content_str = str(content_str)[:500]
            
            compress_text_parts.append(f"### {role_label} (第{i+1}条)\n{content_str}")

        compress_text = "\n\n".join(compress_text_parts)

        # 构建压缩提示词
        compress_prompt = (
            "你是一个专业的对话摘要助手。你的任务是将一段多轮对话压缩成简洁的摘要，"
            "同时保留所有关键信息和上下文连贯性。\n\n"
            "## 压缩规则\n"
            "1. **保留关键信息**：任务目标、已完成的步骤、重要决策、文件路径、代码结构、错误信息\n"
            "2. **保留工具调用结果**：哪些工具被调用、返回了什么关键数据\n"
            "3. **保留问题 - 解决对应关系**：如果对话中包含问题和解决方案，都要保留\n"
            "4. **简洁表达**：用 1-2 句话概括多轮交互，去除冗余重复\n"
            "5. **保持上下文**：确保摘要能让人理解对话的进展和当前状态\n\n"
            "## 输出格式\n"
            "输出一段连贯的摘要文本，200-500 字。使用以下结构：\n"
            "- **任务背景**：1 句话说明任务目标\n"
            "- **已完成工作**：列出已执行的关键步骤和结果\n"
            "- **关键发现**：重要的代码结构、文件路径、接口定义等\n"
            "- **待继续工作**：如果有未完成的步骤，简要说明\n\n"
            f"## 待压缩的对话\n{compress_text[:15000]}"  # 限制输入大小
        )

        system_prompt = (
            "你是对话摘要专家，擅长从技术对话中提取关键信息并生成简洁摘要。"
            "输出纯文本摘要，不要 JSON，不要 markdown。"
        )

        try:
            # 调用 LLM 生成摘要
            logger.info(f"    {tag} 正在调用 LLM 生成摘要...")
            response = self.call(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": compress_prompt}],
                label=f"{tag}/摘要",
            )
            
            summary = response.content.strip()
            if not summary:
                logger.warning(f"    {tag} LLM 生成摘要为空")
                return False

            logger.info(f"    {tag} 摘要生成成功 ({len(summary)} 字符)")

            # 构建压缩后的条目列表
            summary_entry = ConversationEntry(
                role="system",
                content=f"[对话摘要] 以下是对早期 {len(entries_to_compress)} 轮对话的摘要:\n\n{summary}",
            )

            # 保留 system prompt（如果有）+ 摘要 + 剩余条目
            if start_idx > 0:
                conversation_log.entries = [entries[0], summary_entry] + remaining_entries
            else:
                conversation_log.entries = [summary_entry] + remaining_entries

            new_count = len(conversation_log.entries)
            logger.info(
                f"    {tag} 上下文压缩完成：{original_count} -> {new_count} 条 "
                f"(压缩 {len(entries_to_compress)} 条为 1 条摘要)"
            )
            return True

        except Exception as e:
            logger.warning(f"    {tag} 上下文压缩失败：{e}")
            return False

