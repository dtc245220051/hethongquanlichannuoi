"""Bounded retry policy shared by Gemini chat and structured analysis."""

from __future__ import annotations

import email.utils
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

from google.genai import errors

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class GeminiRetryPolicy:
    attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 8.0
    exp_base: float = 2.0
    jitter: float = 0.2
    retry_status_codes: frozenset[int] = frozenset({429, 502, 503, 504})

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("Gemini retry attempts must be at least 1.")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ValueError("Gemini retry delays must not be negative.")
        if self.exp_base <= 1:
            raise ValueError("Gemini retry exp_base must be greater than 1.")
        if not 0 <= self.jitter <= 1:
            raise ValueError("Gemini retry jitter must be between 0 and 1.")


DEFAULT_GEMINI_RETRY_POLICY = GeminiRetryPolicy()


def _error_status(error: errors.APIError) -> int | None:
    return error.code if isinstance(error.code, int) else None


def _retry_after_seconds(
    error: errors.APIError,
    *,
    now: datetime | None = None,
) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw_value = headers.get("retry-after")
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        seconds = (retry_at - reference).total_seconds()
    return max(seconds, 0.0)


def _backoff_seconds(
    attempt: int,
    *,
    policy: GeminiRetryPolicy,
    random_value: float,
) -> float:
    base = policy.initial_delay * (policy.exp_base ** (attempt - 1))
    jitter = base * policy.jitter * ((2 * random_value) - 1)
    return max(0.0, min(base + jitter, policy.max_delay))


def retry_gemini_call(
    call: Callable[[], T],
    *,
    operation: str,
    policy: GeminiRetryPolicy = DEFAULT_GEMINI_RETRY_POLICY,
    sleeper: Callable[[float], None] | None = None,
    random_value: Callable[[], float] = random.random,
    clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> T:
    if sleeper is None:
        sleeper = time.sleep
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except errors.APIError as exc:
            status = _error_status(exc)
            if status not in policy.retry_status_codes:
                setattr(exc, "gemini_attempts", attempt)
                raise
            if attempt >= policy.attempts:
                setattr(exc, "gemini_attempts", attempt)
                raise
            retry_after = _retry_after_seconds(exc)
            delay = (
                min(retry_after, policy.max_delay)
                if retry_after is not None
                else _backoff_seconds(
                    attempt,
                    policy=policy,
                    random_value=random_value(),
                )
            )
            if deadline is not None and clock() + delay >= deadline:
                setattr(exc, "gemini_attempts", attempt)
                logger.warning(
                    "Gemini retry stopped at deadline: operation=%s "
                    "attempt=%s/%s status=%s",
                    operation,
                    attempt,
                    policy.attempts,
                    status,
                )
                raise
            logger.warning(
                "Gemini retry scheduled: operation=%s attempt=%s/%s "
                "status=%s delay=%.2fs retry_after=%s",
                operation,
                attempt,
                policy.attempts,
                status,
                delay,
                retry_after is not None,
            )
            sleeper(delay)
