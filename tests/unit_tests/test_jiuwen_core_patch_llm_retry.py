from __future__ import annotations

from jiuwenclaw.jiuwen_core_patch import LlmRetryConfig, RetryMixin


def _cfg(**kwargs) -> LlmRetryConfig:
    return LlmRetryConfig(enabled=True, max_attempts=3, retry_on_rate_limit=True, **kwargs)


def _retry_mixin() -> RetryMixin:
    return RetryMixin()


class TestInvokeRetryClassification:
    """invoke 包装文案不应阻断可恢复错误的重试。"""

    def test_connection_error_wrapped_as_async_invoke_is_retryable(self):
        exc = Exception(
            "[181001] model call failed, reason: openAI API async invoke error: Connection error."
        )
        assert _retry_mixin()._is_retryable_error(exc, _cfg()) is True

    def test_async_stream_connection_error_remains_retryable(self):
        exc = Exception(
            "[181001] model call failed, reason: openAI API async stream error: Connection error."
        )
        assert _retry_mixin()._is_retryable_error(exc, _cfg()) is True

    def test_auth_error_wrapped_as_async_invoke_still_non_retryable(self):
        exc = Exception(
            "[181001] model call failed, reason: openAI API async invoke error: 401 unauthorized"
        )
        assert _retry_mixin()._is_retryable_error(exc, _cfg()) is False

    def test_unknown_invoke_error_without_retryable_keyword_is_non_retryable(self):
        exc = Exception(
            "[181001] model call failed, reason: openAI API async invoke error: WeirdProviderFault"
        )
        assert _retry_mixin()._is_retryable_error(exc, _cfg()) is False
