from loguru import logger
from urllib.parse import urlparse


def parse_url(url: str):
    try:
        parsed = urlparse(url)
        return {
            "hostname": parsed.hostname,
            "port": parsed.port,
            "scheme": parsed.scheme,
            "path": parsed.path,
            "query": parsed.query,
        }
    except Exception as e:
        logger.error(f"Error parsing url: {e}")
        return None
