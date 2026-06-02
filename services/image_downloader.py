"""Image download service.

Downloads product images from remote URLs and saves them to the local
``resources/{marketplace}/{sku}/`` directory.

File naming: ``1.jpg``, ``2.webp``, … (1-based, preserving the original
extension; defaults to ``.jpg`` when the extension is unknown or absent).

Already-downloaded files are skipped (idempotent).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import aiofiles
from curl_cffi.requests.session import AsyncSession
from loguru import logger

# Base directory for downloaded images, relative to the project root.
_RESOURCES_BASE = Path(__file__).parent.parent / "resources"

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Map response Content-Type → file extension (authoritative over the URL, which
# may say .jpg while the CDN actually returns WebP, e.g. citilink/imgproxy).
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


def _ext_from_content_type(content_type: str | None) -> str | None:
    """Return the file extension for a response Content-Type, or None."""
    ct = (content_type or "").split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(ct)

_HEADERS = {
    "accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "accept-language": "ru-RU,ru;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}


def _safe_name(value: str) -> str:
    """Strip characters that are unsafe in directory / file names."""
    return re.sub(r'[^\w.\-]', '_', value)


def _get_extension(url: str) -> str:
    """Return the lowercase file extension from *url*, falling back to ``.jpg``."""
    path = url.split("?")[0].rstrip("/")
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _ALLOWED_EXTENSIONS else ".jpg"


async def download_images(
    marketplace: str,
    sku: str,
    image_urls: list[str],
) -> list[str]:
    """Download *image_urls* and save them under ``resources/{marketplace}/{sku}/``.

    Returns a list of relative local paths for every successfully saved file.
    Already-present files are included in the result without re-downloading.
    """
    if not image_urls:
        return []

    target_dir = _RESOURCES_BASE / _safe_name(marketplace) / _safe_name(sku)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Per-source download context: some marketplaces (e.g. citilink) serve images
    # from an anti-bot CDN that needs browser-seeded cookies + a referer + proxy.
    headers = dict(_HEADERS)
    cookies = None
    proxy = None
    try:
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415

        crawler = CrawlerRegistry.get(marketplace)()
        if hasattr(crawler, "download_context"):
            ctx = await crawler.download_context() or {}
            headers.update(ctx.get("headers") or {})
            cookies = ctx.get("cookies") or None
            proxy = ctx.get("proxy") or None
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"No download context for {marketplace}: {exc}")

    local_paths: list[str] = []

    async with AsyncSession(impersonate="chrome136") as session:
        for index, url in enumerate(image_urls, start=1):
            # Idempotency: skip if any "{index}.<ext>" already exists (the
            # extension is decided from Content-Type, so glob across extensions).
            existing = sorted(target_dir.glob(f"{index}.*"))
            if existing:
                rel_path = str(existing[0].relative_to(Path(__file__).parent.parent))
                logger.debug(f"Image already exists, skipping: {rel_path}")
                local_paths.append(rel_path)
                continue

            try:
                response = await session.get(
                    url, headers=headers, cookies=cookies, proxy=proxy, timeout=30
                )
                if response.status_code != 200:
                    logger.warning(
                        f"Image download HTTP {response.status_code} for {url}"
                    )
                    continue

                ext = _ext_from_content_type(response.headers.get("content-type")) or _get_extension(url)
                filepath = target_dir / f"{index}{ext}"
                rel_path = str(filepath.relative_to(Path(__file__).parent.parent))

                async with aiofiles.open(filepath, "wb") as fh:
                    await fh.write(response.content)

                local_paths.append(rel_path)
                logger.debug(f"Image saved: {rel_path}")

            except Exception as exc:
                logger.warning(f"Failed to download image {url}: {exc}")

    logger.info(
        f"Images for {marketplace}/{sku}: "
        f"{len(local_paths)}/{len(image_urls)} downloaded → {target_dir}"
    )
    return local_paths
