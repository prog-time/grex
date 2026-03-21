from __future__ import annotations

import os
import re
import random
import aiofiles  # type: ignore[import-untyped]
from loguru import logger
from urllib.parse import urlparse

from config import settings, data_path

# Redis key that holds the serialised proxy list (JSON array of strings).
_PROXY_REDIS_KEY = "proxy:pool"
_PROXY_REDIS_TTL = 60  # seconds — auto-reload when the key expires


class ProxyManager:
    """Manages the proxy pool.

    Proxies are stored in Redis (key ``proxy:pool``, TTL 60 s) so that
    multiple worker processes share a single pool.  When the key is absent
    Redis is not available or proxies haven't been loaded yet, the list is
    loaded from ``proxy.txt`` and cached.

    The old ``cls.proxies`` class-level list has been removed in favour of
    Redis-backed storage.
    """

    @classmethod
    def split_proxy_string(cls, proxy_line: str) -> dict:
        proxy_type: str = settings.get("proxy", {}).get("type", "http")

        if "://" not in proxy_line:
            proxy_line = f"{proxy_type}://{proxy_line}"

        parsed = urlparse(proxy_line)

        return {
            "proxy_type": parsed.scheme,
            "login": parsed.username,
            "password": parsed.password,
            "server": parsed.hostname,
            "port": parsed.port,
        }

    @classmethod
    async def load_proxies(cls) -> list[str]:
        """Read proxies from ``proxy.txt``, validate and cache in Redis.

        Returns the loaded list.  Raises on missing/empty file.
        """
        from services.redis_client import set_json  # noqa: PLC0415 (avoid circular at import)

        proxy_file = os.path.join(data_path, "proxy.txt")
        if not os.path.exists(proxy_file):
            raise FileNotFoundError(f"Proxy file not found: {proxy_file}")

        proxies: list[str] = []
        async with aiofiles.open(proxy_file) as f:
            async for line in f:
                proxy_line = line.strip()
                if proxy_line:
                    if not cls.is_valid_proxy(proxy_line):
                        raise ValueError(
                            f"Invalid proxy {proxy_line}, fix proxy.txt file"
                        )
                    proxies.append(proxy_line)

        if not proxies:
            raise ValueError(
                "The proxy.txt file is empty or does not contain valid proxies"
            )

        await set_json(_PROXY_REDIS_KEY, proxies, ttl=_PROXY_REDIS_TTL)
        logger.debug(f"Proxies loaded and cached in Redis: {len(proxies)}")
        return proxies

    @classmethod
    async def get_all_proxies(cls) -> list[str]:
        """Return the current proxy list from Redis (load if necessary)."""
        from services.redis_client import get_json  # noqa: PLC0415

        proxies: list[str] | None = await get_json(_PROXY_REDIS_KEY)
        if proxies is None:
            proxies = await cls.load_proxies()
        return proxies

    @classmethod
    async def get_random_proxy(cls) -> str | None:
        if not settings.get("proxy", {}).get("active", False):
            return None

        proxies = await cls.get_all_proxies()
        proxy = random.choice(proxies)
        proxy_type: str = settings.get("proxy", {}).get("type", "http").lower()

        if "://" not in proxy:
            proxy = f"{proxy_type}://{proxy}"

        return proxy

    @staticmethod
    def is_valid_proxy(proxy_line: str) -> bool:
        pattern = re.compile(
            r"^(?:(?P<protocol>.+)://)?(?:(?P<login>[^:]+):(?P<password>[^@:]+)@)?"
            r"(?P<host>[^:@]+):(?P<port>\d+)$"
        )
        return bool(pattern.match(proxy_line))
