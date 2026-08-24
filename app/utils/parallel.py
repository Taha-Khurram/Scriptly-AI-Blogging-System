"""
Parallel execution utilities for running multiple AI calls concurrently.
Uses ThreadPoolExecutor since Gemini SDK is synchronous.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Dict, Any, Tuple
import time
from app.core.logging import get_logger

logger = get_logger(__name__)


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
            future = executor.submit(func, *args, **kwargs)
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
            future = executor.submit(func, *args)
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
