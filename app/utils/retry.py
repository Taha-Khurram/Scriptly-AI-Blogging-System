"""
Retry utility for handling transient Firestore/network failures.
"""
import time
from functools import wraps


def with_retry(max_retries=2, delay=0.5, backoff=2.0, exceptions=(Exception,)):
    """
    Retry decorator for functions that may fail due to transient network issues.

    Args:
        max_retries: Number of retry attempts (total calls = 1 + max_retries)
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch and retry
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise last_exception
        return wrapper
    return decorator


def retry_on_unavailable(func):
    """Shortcut decorator: retry twice with 0.3s initial delay for gRPC unavailable errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                if 'unavailable' in error_str or 'deadline' in error_str or 'timeout' in error_str or 'transport' in error_str:
                    last_exception = e
                    if attempt < 2:
                        time.sleep(0.3 * (attempt + 1))
                    continue
                raise
        raise last_exception
    return wrapper
