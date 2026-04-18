"""Process-pool executor for CPU-bound scipy work.

Why:
  - Math-layer ops (`analyze_bank`, `deviation_report`, `anchor_interpolate`)
    spend 0.1–2 s per call inside NumPy / SciPy. Running them on the asyncio
    event loop wedges every concurrent request — opening the UI used to queue
    ~1000 `anchor_interpolate` calls behind one `deviation_report` request and
    freeze all other endpoints for ten minutes.
  - A `ProcessPoolExecutor` isolates that work on real OS processes so the
    event loop stays responsive and each worker runs in parallel on a core.
  - Workers are safe to pickle because piano_core uses frozen `@dataclass`
    models with slots — zero-arg reconstruction, cheap copy.

Lifecycle:
  - `get_pool()` lazily spawns workers on first use (avoids the 1–2 s/worker
    cold-start cost during `uvicorn` boot).
  - The FastAPI lifespan calls `shutdown_pool()` on app shutdown so the
    wrapper's Ctrl+C exits without leaking worker processes.

Usage:
    from piano_web.workers import run_in_pool
    result = await run_in_pool(analyze_bank, bank)
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar


logger = logging.getLogger(__name__)

T = TypeVar("T")


def _default_worker_count() -> int:
    cpu = os.cpu_count() or 2
    # Leave one core for the event loop + DB + OS.
    return max(2, cpu - 1)


_pool: ProcessPoolExecutor | None = None


def get_pool() -> ProcessPoolExecutor:
    """Return the process pool, creating it on first call."""
    global _pool
    if _pool is None:
        workers = _default_worker_count()
        _pool = ProcessPoolExecutor(max_workers=workers)
        logger.info("workers.pool_created", extra={"max_workers": workers})
    return _pool


async def run_in_pool(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run `fn(*args, **kwargs)` in the process pool.

    `fn` and all args/kwargs must be picklable. Use top-level module
    functions — closures and lambdas are not picklable on Windows spawn.
    """
    loop = asyncio.get_running_loop()
    call = partial(fn, *args, **kwargs) if (args or kwargs) else fn
    return await loop.run_in_executor(get_pool(), call)


def shutdown_pool() -> None:
    """Cancel pending futures and tear down the pool. Idempotent."""
    global _pool
    if _pool is None:
        return
    pool = _pool
    _pool = None
    try:
        # cancel_futures drops queued work; running workers still complete.
        pool.shutdown(wait=False, cancel_futures=True)
        logger.info("workers.pool_shutdown")
    except Exception as exc:
        logger.warning("workers.pool_shutdown_failed", extra={"detail": str(exc)})
