from .parsing import ParsingService

# redis and stats are optional at import time (require redis[asyncio] installed).
try:
    from .redis_client import get_redis_client
    from .stats import StatsCollector
except ImportError:
    get_redis_client = None  # type: ignore[assignment,misc]
    StatsCollector = None  # type: ignore[assignment,misc]

__all__ = [
    "ParsingService",
    "get_redis_client",
    "StatsCollector",
]
