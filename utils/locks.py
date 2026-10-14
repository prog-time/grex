"""Per-key asyncio locks for check-then-insert sequences (e.g. per API key limits).

An ``asyncio.Lock`` belongs to the event loop it was first awaited in, so locks
are kept per loop: the server runs one loop, tests create a new one per test.
The API runs as a single process — these locks do not coordinate processes.
"""
from __future__ import annotations

import asyncio
import weakref

_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def key_lock(namespace: str, key: str) -> asyncio.Lock:
    """The lock for *key* within *namespace* in the running event loop."""
    loop_locks = _locks.setdefault(asyncio.get_running_loop(), {})
    return loop_locks.setdefault((namespace, key), asyncio.Lock())
