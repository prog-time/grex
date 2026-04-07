from .parsing import ParsingService
from .license import (
    is_source_allowed,
    get_allowed_sources,
    load_config_from_response,
    load_config_from_cache,
)
from .license_client import activate_device, fetch_license_config

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
    "is_source_allowed",
    "get_allowed_sources",
    "load_config_from_response",
    "load_config_from_cache",
    "activate_device",
    "fetch_license_config",
]
