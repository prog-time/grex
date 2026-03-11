import os
import re
import random
import asyncio
import aiofiles
from loguru import logger
from urllib.parse import urlparse

from config import settings, data_path


class ProxyManager:
    proxies = []
    _proxy_lock = asyncio.Lock()

    @classmethod
    def split_proxy_string(cls, proxy_line: str):
        proxy_type = settings.get("proxy", {}).get("type", "http")

        if '://' not in proxy_line:
            proxy_line = f"{proxy_type}://{proxy_line}"

        parsed = urlparse(proxy_line)

        proxy_type = parsed.scheme
        login = parsed.username
        password = parsed.password
        server = parsed.hostname
        port = parsed.port

        return {
            "proxy_type": proxy_type,
            "login": login,
            "password": password,
            "server": server,
            "port": port
        }

    @classmethod
    async def load_proxies(cls):
        async with cls._proxy_lock:
            if not cls.proxies:
                proxy_file = os.path.join(data_path, "proxy.txt")
                if not os.path.exists(proxy_file):
                    raise FileNotFoundError(f"Proxy file not found: {proxy_file}")

                async with aiofiles.open(proxy_file) as f:
                    async for line in f:
                        proxy_line = line.strip()
                        if proxy_line:
                            if not cls.is_valid_proxy(proxy_line):
                                raise ValueError(f"Invalid proxy {proxy_line}, fix proxy.txt file")
                            cls.proxies.append(proxy_line)

                if not cls.proxies:
                    raise ValueError("The proxy.txt file is empty or does not contain valid proxies")

                logger.debug(f"Proxies loaded: {len(cls.proxies)}")

    @classmethod
    async def get_random_proxy(cls):
        if not settings.get("proxy", {}).get("active", False):
            return None

        if not cls.proxies:
            await cls.load_proxies()

        proxy = random.choice(cls.proxies)
        proxy_type = settings.get("proxy", {}).get("type", "http").lower()

        if '://' not in proxy:
            proxy = f"{proxy_type}://{proxy}"

        return proxy

    @staticmethod
    def is_valid_proxy(proxy_line: str):
        pattern = re.compile(
            r'^(?:(?P<protocol>.+)://)?(?P<login>[^:]+):(?P<password>[^@:]+)@(?P<host>[^:]+):(?P<port>\d+)$')

        match = pattern.match(proxy_line)
        if not match:
            return False

        return True
