"""Resilience helpers for data adapters (spec §6/§0: retry, timeout, fail-closed)."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from ..logging_setup import get_logger

_log = get_logger("data")

T = TypeVar("T")


class DataUnavailable(Exception):
    """Raised when a datum cannot be fetched/computed. Caller treats as 'unknown'."""


def with_retry(
    attempts: int = 3,
    base_delay: float = 0.5,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Retry decorator with exponential backoff. Fails CLOSED: after the final
    attempt it returns None (the 'unknown' sentinel) rather than raising, so an
    upstream data outage degrades to PASS, never to a fabricated trade."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T | None]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T | None:
            delay = base_delay
            for i in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001 — deliberate fail-closed
                    _log.warning("%s attempt %d/%d failed: %s",
                                 fn.__name__, i, attempts, exc)
                    if i == attempts:
                        _log.error("%s exhausted retries -> returning unknown",
                                   fn.__name__)
                        return None
                    time.sleep(delay)
                    delay *= 2
            return None

        return wrapper

    return decorator
