"""Cross-cutting utilities.

Deliberately re-exports nothing. A package ``__init__`` that re-exports its
submodules' names creates two valid import paths for one object, and here that
is actively dangerous: ``from app.utils import cache`` would resolve to the
*module* when the submodule has been imported and to the *singleton* when only
the re-export has, which is a bug that only appears depending on import order.

Import from the submodule that owns the name::

    from app.utils.cache import cache, cached
    from app.utils.date_utils import utcnow, ensure_aware, to_utc
    from app.utils.parallel import run_parallel_simple
    from app.utils.task_manager import task_manager
"""
