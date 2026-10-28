from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from livestock_app.gemini_retry import (
    DEFAULT_GEMINI_RETRY_POLICY,
    retry_gemini_call,
)


def _api_error(
    status: int,
    *,
    retry_after: str | None = None,
) -> errors.APIError:
    response = (
        SimpleNamespace(headers={"retry-after": retry_after})
        if retry_after is not None
        else None
    )
    return errors.APIError(
        status,
        {"error": {"message": "provider detail"}},
        response,
    )


def test_retry_503_then_succeeds():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise _api_error(503)
        return "ok"

    result = retry_gemini_call(
        call,
        operation="unit",
        sleeper=sleeps.append,
        random_value=lambda: 0.5,
    )

    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_429_honors_retry_after():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise _api_error(429, retry_after="2")
        return "ok"

    result = retry_gemini_call(
        call,
        operation="unit",
        sleeper=sleeps.append,
        random_value=lambda: 0.5,
    )

    assert result == "ok"
    assert sleeps == [2.0]


def test_retry_after_is_capped_by_policy():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise _api_error(503, retry_after="120")
        return "ok"

    retry_gemini_call(
        call,
        operation="unit",
        sleeper=sleeps.append,
        random_value=lambda: 0.5,
    )

    assert sleeps == [DEFAULT_GEMINI_RETRY_POLICY.max_delay]


def test_retry_exhaustion_is_bounded():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        raise _api_error(503)

    with pytest.raises(errors.APIError) as error:
        retry_gemini_call(
            call,
            operation="unit",
            sleeper=sleeps.append,
            random_value=lambda: 0.5,
        )

    assert len(calls) == DEFAULT_GEMINI_RETRY_POLICY.attempts
    assert sleeps == [1.0, 2.0]
    assert error.value.gemini_attempts == DEFAULT_GEMINI_RETRY_POLICY.attempts


def test_non_retryable_status_is_not_retried():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        raise _api_error(401)

    with pytest.raises(errors.APIError) as error:
        retry_gemini_call(
            call,
            operation="unit",
            sleeper=sleeps.append,
            random_value=lambda: 0.5,
        )

    assert len(calls) == 1
    assert sleeps == []
    assert error.value.gemini_attempts == 1


def test_timeout_is_not_retried():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        raise httpx.ReadTimeout("provider detail")

    with pytest.raises(httpx.ReadTimeout):
        retry_gemini_call(
            call,
            operation="unit",
            sleeper=sleeps.append,
            random_value=lambda: 0.5,
        )

    assert len(calls) == 1
    assert sleeps == []


def test_deadline_prevents_extra_retry():
    calls = []
    sleeps = []

    def call():
        calls.append(1)
        raise _api_error(503)

    with pytest.raises(errors.APIError) as error:
        retry_gemini_call(
            call,
            operation="unit",
            sleeper=sleeps.append,
            random_value=lambda: 0.5,
            clock=lambda: 0.0,
            deadline=0.5,
        )

    assert len(calls) == 1
    assert sleeps == []
    assert error.value.gemini_attempts == 1
