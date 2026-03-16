"""LLM 重试策略测试"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_system.services.conversation_logger import ConversationLog


class _FakeConnError(Exception):
    """用于替代 anthropic.APIConnectionError 的测试异常"""


class _FakeStatusError(Exception):
    """用于模拟供应商返回的请求体过大错误"""


class _FakeStream:
    """最小 stream 上下文管理器"""

    def __init__(self, response: object) -> None:
        self._response = response

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def __iter__(self):
        return iter([])

    def get_final_message(self) -> object:
        return self._response


class _FakeMessages:
    """模拟 messages.stream 行为：前 N 次抛错，之后成功"""

    def __init__(self, fail_count: int, response: object) -> None:
        self._remaining_failures = fail_count
        self._response = response

    def stream(self, **kwargs):
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise _FakeConnError("connection failed")
        return _FakeStream(self._response)


class _FakeClient:
    """最小 Anthropic 客户端替身"""

    def __init__(self, fail_count: int, response: object) -> None:
        self.messages = _FakeMessages(fail_count=fail_count, response=response)


class _FakeTimeoutMessages:
    """模拟 messages.stream 行为：前 N 次抛 TimeoutError，之后成功"""

    def __init__(self, fail_count: int, response: object) -> None:
        self._remaining_failures = fail_count
        self._response = response

    def stream(self, **kwargs):
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise TimeoutError("The read operation timed out")
        return _FakeStream(self._response)


class _FakeTimeoutClient:
    """最小 Anthropic 客户端替身（底层超时版本）"""

    def __init__(self, fail_count: int, response: object) -> None:
        self.messages = _FakeTimeoutMessages(fail_count=fail_count, response=response)


def _make_service(client: object):
    from agent_system.services.llm import LLMService, TokenUsage

    service = object.__new__(LLMService)
    service._client = client
    service._model = "test-model"
    service._max_tokens = 1024
    service._temperature = 0.0
    service._usage = TokenUsage()
    service._timeout = 30.0
    service._max_retries = 0
    return service


class _DummyToolExecutor:
    def execute(self, name: str, tool_input: dict[str, object]) -> str:
        return "ok"


def test_backoff_doubles_until_two_minutes_and_keeps_capped(monkeypatch) -> None:
    """重试间隔按 10s 翻倍，达到 120s 后保持不变"""
    from agent_system.services import llm as llm_module

    waits: list[int] = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: waits.append(int(seconds)))
    monkeypatch.setattr(llm_module.anthropic, "APIConnectionError", _FakeConnError)

    expected_response = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=2), stop_reason="")
    service = _make_service(_FakeClient(fail_count=8, response=expected_response))

    result = service._call_with_retry(model="x", messages=[])

    assert result is expected_response
    assert waits == [10, 20, 40, 80, 120, 120, 120, 120]


def test_read_operation_timeout_is_retried(monkeypatch) -> None:
    """底层 TimeoutError('The read operation timed out') 也会重试"""
    from agent_system.services import llm as llm_module

    waits: list[int] = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: waits.append(int(seconds)))

    expected_response = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=3, output_tokens=5), stop_reason="")
    service = _make_service(_FakeTimeoutClient(fail_count=3, response=expected_response))

    result = service._call_with_retry(model="x", messages=[])

    assert result is expected_response
    assert waits == [10, 20, 40]


def test_estimate_request_payload_contains_size_metrics() -> None:
    """请求体估算应返回排查超限所需关键指标"""
    from agent_system.services.llm import _estimate_request_payload

    payload = _estimate_request_payload(
        system_prompt="system prompt",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
    )

    assert payload["message_count"] == 2
    assert payload["tool_count"] == 1
    assert payload["message_chars"] >= 10
    assert payload["payload_bytes"] > 0


def test_extract_input_length_limit_reads_provider_limit() -> None:
    """应能从供应商 400 错误中提取最大输入长度。"""
    from agent_system.services.llm import _extract_input_length_limit

    error = _FakeStatusError(
        "Error code: 400 - {'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
        "Range of input length should be [1, 983616]'}}"
    )

    assert _extract_input_length_limit(error) == 983616


def test_call_retries_with_smaller_payload_after_provider_limit_error(monkeypatch) -> None:
    """供应商报输入长度超限时，应自动收紧请求体上限并重试。"""
    from agent_system.services.llm import LLMService, TokenUsage

    service = object.__new__(LLMService)
    service._model = "test-model"
    service._max_tokens = 1024
    service._temperature = 0.0
    service._usage = TokenUsage()
    service._timeout = 30.0
    service._max_retries = 0
    service._request_max_bytes = 5_500_000

    call_payload_sizes: list[int] = []
    response = SimpleNamespace(
        content=[],
        usage=SimpleNamespace(input_tokens=1, output_tokens=2),
        stop_reason="",
    )

    def _fake_call_with_retry(label: str = "", **kwargs):
        payload_size = len(json.dumps({
            "system": kwargs.get("system"),
            "messages": kwargs.get("messages"),
            "tools": kwargs.get("tools", []),
        }, ensure_ascii=False).encode("utf-8"))
        call_payload_sizes.append(payload_size)
        if len(call_payload_sizes) == 1:
            raise _FakeStatusError(
                "Error code: 400 - {'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
                "Range of input length should be [1, 983616]'}}"
            )
        return response

    service._call_with_retry = _fake_call_with_retry  # type: ignore[method-assign]

    result = service.call(
        system_prompt="review prompt",
        messages=[{"role": "user", "content": "X" * 1_200_000}],
        tools=None,
        enable_cache=False,
    )

    assert result.input_tokens == 1
    assert len(call_payload_sizes) == 2
    assert call_payload_sizes[0] > 983_616
    assert call_payload_sizes[1] < call_payload_sizes[0]
    assert service._request_max_bytes < 983_616


def test_tools_loop_done_reflection_triggers_finalization(monkeypatch) -> None:
    """软限制触发 DONE 时，应再补一次无工具收尾调用，避免返回过程性文本"""
    from agent_system.services.llm import LLMResponse, TokenUsage
    from agent_system.services.llm import LLMService

    service = object.__new__(LLMService)
    service._usage = TokenUsage()

    responses = [
        LLMResponse(
            content="发现魔术字符串，继续检查 new 依赖",
            tool_calls=[{"id": "tool-1", "name": "grep_content", "input": {"path": "a.ts"}}],
        ),
        LLMResponse(content="DONE: 已完成所有审查"),
        LLMResponse(content='{"passed": false, "issues": ["magic string"], "suggestions": [], "context_for_coder": ""}'),
    ]

    def _fake_call(system_prompt, messages, tools=None, conversation_log=None, label="", enable_cache=True):
        return responses.pop(0)

    service.call = _fake_call  # type: ignore[method-assign]

    result = service.call_with_tools_loop(
        system_prompt="reviewer prompt",
        messages=[{"role": "user", "content": "review this"}],
        tools=[{"name": "grep_content"}],
        tool_executor=_DummyToolExecutor(),
        max_iterations=5,
        soft_limit=1,
        conversation_log=None,
        label="Reviewer/T1.7",
    )

    assert result.content.startswith('{"passed": false')


def test_fit_messages_to_payload_trims_large_tool_results() -> None:
    """请求体过大时应优先裁剪旧的 tool_result 内容，而不是直接把请求打到 413。"""
    from agent_system.services.llm import _fit_messages_to_payload

    huge_tool_result = "X" * 20000
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": huge_tool_result,
                }
            ],
        },
        {"role": "assistant", "content": "final answer"},
    ]

    fitted_messages, payload, was_trimmed = _fit_messages_to_payload(
        system_prompt="review prompt",
        messages=messages,
        tools=None,
        max_bytes=5000,
    )

    assert was_trimmed is True
    assert payload["payload_bytes"] <= 5000
    trimmed_content = fitted_messages[0]["content"][0]["content"]
    assert len(trimmed_content) < len(huge_tool_result)


def test_tools_loop_summarizes_old_messages_into_system_prompt() -> None:
    """请求体达到字节阈值时，应先做滚动摘要，再用摘要后的 system prompt 发起正式请求。"""
    from agent_system.services.llm import LLMResponse, TokenUsage
    from agent_system.services.llm import LLMService

    service = object.__new__(LLMService)
    service._usage = TokenUsage()

    captured_calls: list[dict[str, object]] = []

    def _fake_call(system_prompt, messages, tools=None, conversation_log=None, label="", enable_cache=True):
        captured_calls.append({
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
            "label": label,
        })
        if str(label).endswith("/摘要"):
            return LLMResponse(content="任务是修复审查问题，已读多个文件，确认关键风险在 magic string，后续只需给出最终结论。")
        return LLMResponse(content="final answer", tool_calls=[])

    service.call = _fake_call  # type: ignore[method-assign]

    messages = [
        {"role": "user", "content": f"message-{index}-" + ("x" * 400_000)}
        for index in range(26)
    ]

    result = service.call_with_tools_loop(
        system_prompt="reviewer prompt",
        messages=messages,
        tools=[{"name": "read_file"}],
        tool_executor=_DummyToolExecutor(),
        max_iterations=3,
        soft_limit=30,
        conversation_log=None,
        label="Reviewer/T9.1",
    )

    assert result.content == "final answer"
    assert len(captured_calls) == 2
    assert str(captured_calls[0]["label"]).endswith("/摘要")
    assert "[CONTEXT SUMMARY START]" in str(captured_calls[1]["system_prompt"])
    assert len(captured_calls[1]["messages"]) < len(messages)


def test_tools_loop_summary_updates_conversation_log() -> None:
    """滚动摘要后，对话日志也应同步为摘要 + 最近消息，而不是保留整段旧历史。"""
    from agent_system.services.llm import LLMResponse, TokenUsage
    from agent_system.services.llm import LLMService

    service = object.__new__(LLMService)
    service._usage = TokenUsage()

    def _fake_call(system_prompt, messages, tools=None, conversation_log=None, label="", enable_cache=True):
        if str(label).endswith("/摘要"):
            return LLMResponse(content="已完成历史对话压缩，保留任务目标、关键文件和未解决问题。")
        return LLMResponse(content="done", tool_calls=[])

    service.call = _fake_call  # type: ignore[method-assign]

    log = ConversationLog(task_id="T-1", agent_name="reviewer")
    initial_messages = [
        {"role": "user", "content": f"user-{index}-" + ("x" * 400_000)}
        for index in range(25)
    ]

    service.call_with_tools_loop(
        system_prompt="reviewer prompt",
        messages=initial_messages,
        tools=[{"name": "read_file"}],
        tool_executor=_DummyToolExecutor(),
        max_iterations=2,
        soft_limit=30,
        conversation_log=log,
        label="Reviewer/T9.2",
    )

    assert log.entries[0].role == "system"
    assert str(log.entries[0].content).startswith("[对话摘要]")
    assert len(log.entries) < len(initial_messages) + 1


def test_tools_loop_does_not_summarize_small_history() -> None:
    """请求体未达到字节阈值时，不应仅因消息条数较多而触发滚动摘要。"""
    from agent_system.services.llm import LLMResponse, TokenUsage
    from agent_system.services.llm import LLMService

    service = object.__new__(LLMService)
    service._usage = TokenUsage()

    captured_labels: list[str] = []

    def _fake_call(system_prompt, messages, tools=None, conversation_log=None, label="", enable_cache=True):
        captured_labels.append(str(label))
        return LLMResponse(content="final answer", tool_calls=[])

    service.call = _fake_call  # type: ignore[method-assign]

    messages = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(40)
    ]

    result = service.call_with_tools_loop(
        system_prompt="reviewer prompt",
        messages=messages,
        tools=[{"name": "read_file"}],
        tool_executor=_DummyToolExecutor(),
        max_iterations=3,
        soft_limit=30,
        conversation_log=None,
        label="Reviewer/T9.3",
    )

    assert result.content == "final answer"
    assert captured_labels == ["Reviewer/T9.3"]
