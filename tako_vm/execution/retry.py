"""
Retry logic with exponential backoff for transient failures.

Provides decorators and utilities for retrying operations that may fail temporarily.
"""

import time
import random
import logging
import functools
from dataclasses import dataclass
from typing import Optional, Callable, Tuple, Type, Set

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    """Maximum number of attempts (including initial attempt)."""

    base_delay: float = 1.0
    """Base delay in seconds between retries."""

    max_delay: float = 30.0
    """Maximum delay in seconds between retries."""

    exponential_base: float = 2.0
    """Base for exponential backoff calculation."""

    jitter: bool = True
    """Add random jitter to prevent thundering herd."""

    jitter_factor: float = 0.1
    """Jitter factor (0.1 = +/- 10% of delay)."""


# Default set of transient errors that should trigger retry
TRANSIENT_ERROR_MESSAGES: Set[str] = {
    "connection refused",
    "connection reset",
    "connection timed out",
    "temporary failure",
    "service unavailable",
    "resource temporarily unavailable",
    "too many requests",
    "rate limit",
    "docker daemon",
    "cannot connect to docker",
    "no space left on device",
}


def is_transient_error(error: Exception) -> bool:
    """
    Check if an error is likely transient and worth retrying.

    Args:
        error: The exception to check

    Returns:
        True if error appears to be transient
    """
    error_msg = str(error).lower()

    for pattern in TRANSIENT_ERROR_MESSAGES:
        if pattern in error_msg:
            return True

    # Check for specific exception types
    error_type = type(error).__name__.lower()
    transient_types = {"timeouterror", "connectionerror", "oserror"}
    if any(t in error_type for t in transient_types):
        return True

    return False


def calculate_delay(
    attempt: int,
    config: RetryConfig
) -> float:
    """
    Calculate delay before next retry attempt.

    Uses exponential backoff with optional jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        config: Retry configuration

    Returns:
        Delay in seconds
    """
    # Exponential backoff
    delay = config.base_delay * (config.exponential_base ** attempt)

    # Cap at max delay
    delay = min(delay, config.max_delay)

    # Add jitter if enabled
    if config.jitter:
        jitter_range = delay * config.jitter_factor
        delay = delay + random.uniform(-jitter_range, jitter_range)

    return max(0, delay)


def retry(
    config: Optional[RetryConfig] = None,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    Decorator for retrying a function with exponential backoff.

    Args:
        config: Retry configuration (uses defaults if not provided)
        retryable_exceptions: Tuple of exception types to retry on
        on_retry: Callback called before each retry with (exception, attempt)

    Returns:
        Decorated function

    Example:
        @retry(RetryConfig(max_attempts=3))
        def fetch_data():
            return api.get("/data")
    """
    if config is None:
        config = RetryConfig()

    if retryable_exceptions is None:
        retryable_exceptions = (Exception,)

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(config.max_attempts):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as e:
                    last_exception = e

                    # Check if this is the last attempt
                    if attempt == config.max_attempts - 1:
                        logger.warning(
                            f"Retry exhausted for {func.__name__} after "
                            f"{config.max_attempts} attempts: {e}"
                        )
                        raise

                    # Check if error is transient
                    if not is_transient_error(e):
                        logger.debug(
                            f"Non-transient error in {func.__name__}, not retrying: {e}"
                        )
                        raise

                    # Calculate delay
                    delay = calculate_delay(attempt, config)

                    logger.info(
                        f"Retry {attempt + 1}/{config.max_attempts} for "
                        f"{func.__name__} after {delay:.2f}s: {e}"
                    )

                    # Call retry callback if provided
                    if on_retry:
                        on_retry(e, attempt + 1)

                    # Wait before retry
                    time.sleep(delay)

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception

        return wrapper
    return decorator


class RetryContext:
    """
    Context manager for retry logic.

    Useful when you need more control than the decorator provides.

    IMPORTANT: This class uses blocking time.sleep() for delays.
    It should only be used in synchronous code or within a ThreadPoolExecutor.
    Do NOT use this directly in async coroutines - the sleep will block
    the entire event loop.

    Example:
        # In synchronous code or thread pool:
        retry_ctx = RetryContext(RetryConfig(max_attempts=3))
        while retry_ctx.should_retry():
            try:
                result = do_something()
                break
            except Exception as e:
                retry_ctx.record_failure(e)
                if not retry_ctx.should_retry():
                    raise
    """

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self.attempt = 0
        self.last_error: Optional[Exception] = None

    def should_retry(self) -> bool:
        """Check if more retries are available."""
        return self.attempt < self.config.max_attempts

    def record_failure(self, error: Exception) -> None:
        """
        Record a failure and wait before next attempt.

        Args:
            error: The exception that occurred
        """
        self.last_error = error
        self.attempt += 1

        if self.should_retry() and is_transient_error(error):
            delay = calculate_delay(self.attempt - 1, self.config)
            logger.info(
                f"Retry {self.attempt}/{self.config.max_attempts} "
                f"after {delay:.2f}s: {error}"
            )
            time.sleep(delay)

    def record_success(self) -> None:
        """Record a successful operation."""
        self.attempt = 0
        self.last_error = None

    @property
    def is_exhausted(self) -> bool:
        """Check if all retry attempts have been used."""
        return self.attempt >= self.config.max_attempts
