"""LLM 重试策略测试"""

from __future__ import annotations

from types import SimpleNamespace


class _FakeConnError(Exception):
    """用于替代 anthropic.APIConnectionError 的测试异常"""


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
