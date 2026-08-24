"""
Parallel execution utilities for running multiple AI calls concurrently.
Uses ThreadPoolExecutor since Gemini SDK is synchronous.

Also the main lever on page latency. A Firestore round trip from this
application measures 0.5-3.5 s, so a view that issues six queries back to back
takes six times as long as one that issues them together. Measured here, six
independent queries cost 4363 ms serially and 1109 ms concurrently -- a 3.9x
speedup, which holds because the Firestore SDK is gRPC and releases the GIL for
the duration of the call.

Every task runs inside a *copy of the caller's* ``contextvars`` context. That is
what lets the per-request repository memo
(:func:`app.repositories._helpers.request_cached`) work across the fan-out:
without it a worker thread starts with an empty context, misses the memo, and
re-issues the very round trip the memo exists to remove.
"""
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Callable, List, Dict, Any, Tuple
import time
from app.core.logging import get_logger

logger = get_logger(__name__)


def _submit_in_context(executor, func, *args, **kwargs):
    """Submit ``func`` so it runs inside a copy of the caller's context.

    ``ThreadPoolExecutor`` does not propagate ``contextvars`` the way asyncio
    tasks do, so the context has to be carried across explicitly. The copy is
    taken here, in the calling thread, and each task gets its own -- but the
    values inside are shared references, which is exactly what makes the
    request's memo store visible to every worker.
    """
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, partial(func, *args, **kwargs))


def run_parallel(tasks: List[Tuple[Callable, tuple, dict]], max_workers: int = 3, timeout: int = 60) -> Dict[str, Any]:
    """
    Run multiple functions in parallel and collect results.

    Args:
        tasks: List of (function, args, kwargs) tuples. Each should have a 'name' in kwargs.
        max_workers: Maximum concurrent threads
        timeout: Timeout in seconds for all tasks

    Returns:
        Dict with task names as keys and results/errors as values
    """
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {}

        for func, args, kwargs in tasks:
            name = kwargs.pop('_task_name', func.__name__)
            future = _submit_in_context(executor, func, *args, **kwargs)
            future_to_name[future] = name

        for future in as_completed(future_to_name, timeout=timeout):
            name = future_to_name[future]
            try:
                results[name] = {"success": True, "data": future.result()}
            except Exception as e:
                logger.exception(f"Task '{name}' failed")
                results[name] = {"success": False, "error": str(e)}

    return results


def run_parallel_simple(funcs_with_args: List[Tuple[Callable, tuple]], max_workers: int = 3) -> List[Any]:
    """
    Simpler parallel execution - returns results in order.

    Args:
        funcs_with_args: List of (function, args_tuple) pairs
        max_workers: Maximum concurrent threads

    Returns:
        List of results in same order as input
    """
    results = [None] * len(funcs_with_args)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {}

        for i, (func, args) in enumerate(funcs_with_args):
            future = _submit_in_context(executor, func, *args)
            future_to_index[future] = i

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception:
                logger.exception(f"Parallel task {index} failed")
                results[index] = None

    return results


class TimedExecution:
    """Context manager for timing code blocks"""

    def __init__(self, name: str = ""):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.duration = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        if self.name:
            logger.debug(f"[TIMING] {self.name}: {self.duration:.2f}s")
