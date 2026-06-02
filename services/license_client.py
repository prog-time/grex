"""HTTP client for fetching license config from api_grex_parser.

Responsibilities:
- activate_device(service_url, api_key, mac_address, timeout) -> bool
- fetch_license_config(service_url, api_key, mac_address, timeout) -> dict | None
- POST to {service_url}/activate — idempotent, call on every startup
- POST to {service_url}/config — returns JWT tokens per allowed source
- Uses curl_cffi AsyncSession (already in project)
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from curl_cffi.requests import AsyncSession


async def activate_device(
    service_url: str,
    api_key: str,
    mac_address: str,
    timeout: int = 5,
) -> bool:
    """Activate this device against api_grex_parser (idempotent).

    Args:
        service_url: Base URL of the licensing service, e.g. "https://api-grex.example.com".
        api_key: Client API key sent in the X-API-Key header.
        mac_address: This machine's MAC address in AA:BB:CC:DD:EE:FF format.
        timeout: Request timeout in seconds.

    Returns:
        True if activation succeeded or device was already active, False on any error.
    """
    url = f"{service_url.rstrip('/')}/activate"

    try:
        async with AsyncSession() as session:
            response = await session.post(
                url,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json={"mac_address": mac_address},
                timeout=timeout,
            )
            response.raise_for_status()
            data: dict = response.json()
            activated: bool = data.get("activated", False)
            if activated:
                logger.debug(f"Device activation OK: {data.get('message', '')}")
            else:
                logger.warning(f"Device activation returned activated=false: {data}")
            return activated

    except Exception as exc:
        logger.warning(f"Failed to activate device at '{url}': {exc}")
        return False


async def fetch_license_config(
    service_url: str,
    api_key: str,
    mac_address: str,
    timeout: int = 5,
) -> Optional[dict]:
    """Fetch license config from api_grex_parser.

    Args:
        service_url: Base URL of the licensing service, e.g. "https://api-grex.example.com".
        api_key: Client API key sent in the X-API-Key header.
        mac_address: This machine's MAC address in AA:BB:CC:DD:EE:FF format.
        timeout: Request timeout in seconds.

    Returns:
        Parsed response dict on success, None on any error.
    """
    url = f"{service_url.rstrip('/')}/config"

    try:
        async with AsyncSession() as session:
            response = await session.post(
                url,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json={"mac_address": mac_address},
                timeout=timeout,
            )
            response.raise_for_status()
            data: dict = response.json()
            logger.debug(f"License config fetched from '{url}'")
            return data

    except Exception as exc:
        logger.warning(f"Failed to fetch license config from '{url}': {exc}")
        return None
