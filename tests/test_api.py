"""Tests for FastAPI endpoints defined in api.py.

Strategy
--------
- Redis / admin setup fails gracefully (the lifespan swallows those errors).
- TestClient is used for synchronous HTTP testing — no running server needed.
- Real marketplace crawlers are NOT invoked; parse_product is mocked where needed.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import api
from models.product import ProductCard

_API_KEY = "4353456dfvbdfg34"   # from data/settings.yml
_AUTH_HEADERS = {"X-API-Key": _API_KEY}


# ---------------------------------------------------------------------------
# Shared TestClient fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient that suppresses server-side exceptions as HTTP 5xx."""
    return TestClient(api.app, raise_server_exceptions=False)


def _fake_product(url: str = "https://ozon.ru/product/test") -> ProductCard:
    return ProductCard(
        title="Test Product",
        sku="SKU-001",
        original_price=Decimal("1999.00"),
        current_price=Decimal("1499.00"),
        images=["https://img.example.com/1.jpg"],
        source_url=url,
    )


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_response_contains_message_key(self, client):
        r = client.get("/")
        assert "message" in r.json()

    def test_message_identifies_service(self, client):
        r = client.get("/")
        assert "Parsing API" in r.json()["message"]

    def test_response_contains_health_link(self, client):
        r = client.get("/")
        assert "health" in r.json()


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_status_is_healthy(self, client):
        r = client.get("/health")
        assert r.json()["status"] == "healthy"

    def test_response_contains_supported_marketplaces(self, client):
        r = client.get("/health")
        body = r.json()
        assert "supported_marketplaces" in body
        assert isinstance(body["supported_marketplaces"], list)
        assert len(body["supported_marketplaces"]) > 0

    def test_all_four_marketplaces_listed(self, client):
        r = client.get("/health")
        marketplaces = r.json()["supported_marketplaces"]
        for name in ("wildberries", "ozon", "yandex_market", "megamarket"):
            assert name in marketplaces

    def test_uptime_is_non_negative(self, client):
        r = client.get("/health")
        assert r.json()["uptime"] >= 0


# ---------------------------------------------------------------------------
# GET /marketplaces
# ---------------------------------------------------------------------------

class TestMarketplacesEndpoint:
    def test_returns_200(self, client):
        r = client.get("/marketplaces")
        assert r.status_code == 200

    def test_response_contains_supported_marketplaces_key(self, client):
        r = client.get("/marketplaces")
        assert "supported_marketplaces" in r.json()

    def test_all_four_marketplaces_present(self, client):
        r = client.get("/marketplaces")
        names = r.json()["supported_marketplaces"]
        for expected in ("wildberries", "ozon", "yandex_market", "megamarket"):
            assert expected in names

    def test_response_contains_example_urls(self, client):
        r = client.get("/marketplaces")
        assert "example_urls" in r.json()
        assert len(r.json()["example_urls"]) > 0


# ---------------------------------------------------------------------------
# GET /parse/detect
# ---------------------------------------------------------------------------

class TestDetectMarketplaceEndpoint:
    @pytest.mark.parametrize("url, expected_marketplace", [
        (
            "https://www.wildberries.ru/catalog/17386348/detail.aspx",
            "wildberries",
        ),
        (
            "https://ozon.ru/product/shumin-maska-1563221610",
            "ozon",
        ),
        (
            "https://market.yandex.ru/card/espander/103757317666",
            "yandex_market",
        ),
        (
            "https://megamarket.ru/catalog/details/puhovik-600011555038",
            "megamarket",
        ),
    ])
    def test_detects_known_marketplace(self, client, url, expected_marketplace):
        r = client.get(f"/parse/detect?url={url}")
        assert r.status_code == 200
        body = r.json()
        assert body["marketplace"] == expected_marketplace
        assert body["status"] == "supported"

    def test_returns_url_in_response(self, client):
        url = "https://www.wildberries.ru/catalog/17386348/detail.aspx"
        r = client.get(f"/parse/detect?url={url}")
        assert r.status_code == 200
        assert "url" in r.json()

    def test_returns_422_for_malformed_url(self, client):
        """FastAPI validates the HttpUrl query param — malformed strings → 422."""
        r = client.get("/parse/detect?url=not-a-valid-url")
        assert r.status_code == 422

    def test_returns_non_200_for_unsupported_marketplace(self, client):
        """An unsupported marketplace URL triggers UnsupportedMarketplaceError.
        The general exception handler returns 500 in this application."""
        r = client.get("/parse/detect?url=https://amazon.com/dp/B08N5WRWNW")
        assert r.status_code in (400, 500)  # handler converts ValueError → 400 or falls back to 500


# ---------------------------------------------------------------------------
# POST /parse — authentication
# ---------------------------------------------------------------------------

class TestParseAuthenticationEndpoint:
    def test_returns_401_when_api_key_header_is_absent(self, client):
        r = client.post("/parse", json={"url": "https://ozon.ru/product/test"})
        assert r.status_code == 401

    def test_401_body_mentions_api_key(self, client):
        r = client.post("/parse", json={"url": "https://ozon.ru/product/test"})
        assert "API key" in r.json().get("detail", "")

    def test_returns_403_when_api_key_is_wrong(self, client):
        r = client.post(
            "/parse",
            json={"url": "https://ozon.ru/product/test"},
            headers={"X-API-Key": "totally-wrong-key"},
        )
        assert r.status_code == 403

    def test_403_body_mentions_invalid_key(self, client):
        r = client.post(
            "/parse",
            json={"url": "https://ozon.ru/product/test"},
            headers={"X-API-Key": "totally-wrong-key"},
        )
        assert "Invalid" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# POST /parse — input validation
# ---------------------------------------------------------------------------

class TestParseInputValidation:
    def test_returns_422_for_unsupported_marketplace_url(self, client):
        """Pydantic's field_validator rejects URLs not from supported domains."""
        r = client.post(
            "/parse",
            json={"url": "https://amazon.com/dp/B08N5WRWNW"},
            headers=_AUTH_HEADERS,
        )
        assert r.status_code == 422

    def test_returns_422_for_missing_url_field(self, client):
        r = client.post("/parse", json={}, headers=_AUTH_HEADERS)
        assert r.status_code == 422

    def test_returns_422_for_non_url_string(self, client):
        r = client.post("/parse", json={"url": "just-a-string"}, headers=_AUTH_HEADERS)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /parse — successful parse (mocked crawler)
# ---------------------------------------------------------------------------

class TestParseSuccessfulResponse:
    def test_returns_200_with_valid_key_and_supported_url(self, client):
        product = _fake_product("https://ozon.ru/product/test")
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.status_code == 200

    def test_response_success_flag_is_true(self, client):
        product = _fake_product("https://ozon.ru/product/test")
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.json()["success"] is True

    def test_response_contains_marketplace_name(self, client):
        product = _fake_product("https://ozon.ru/product/test")
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.json()["marketplace"] == "ozon"

    def test_response_contains_product_data(self, client):
        product = _fake_product("https://ozon.ru/product/test")
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        data = r.json()["data"]
        assert data["title"] == "Test Product"
        assert data["sku"] == "SKU-001"
        assert data["current_price"] == 1499.0
        assert data["original_price"] == 1999.0

    def test_response_contains_processing_time(self, client):
        product = _fake_product("https://ozon.ru/product/test")
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.json()["processing_time"] is not None
        assert r.json()["processing_time"] >= 0

    def test_wildberries_url_is_correctly_identified(self, client):
        url = "https://www.wildberries.ru/catalog/17386348/detail.aspx"
        product = _fake_product(url)
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post("/parse", json={"url": url}, headers=_AUTH_HEADERS)
        assert r.json()["marketplace"] == "wildberries"

    def test_yandex_market_url_is_correctly_identified(self, client):
        url = "https://market.yandex.ru/card/espander/103757317666"
        product = _fake_product(url)
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post("/parse", json={"url": url}, headers=_AUTH_HEADERS)
        assert r.json()["marketplace"] == "yandex_market"

    def test_megamarket_url_is_correctly_identified(self, client):
        url = "https://megamarket.ru/catalog/details/puhovik-600011555038"
        product = _fake_product(url)
        with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
            r = client.post("/parse", json={"url": url}, headers=_AUTH_HEADERS)
        assert r.json()["marketplace"] == "megamarket"


# ---------------------------------------------------------------------------
# POST /parse — crawler failure returns graceful error response
# ---------------------------------------------------------------------------

class TestParseErrorHandling:
    def test_returns_200_with_success_false_when_crawler_raises(self, client):
        """When the crawler raises an unexpected exception the endpoint must
        catch it and return success=False rather than a 5xx status."""
        with patch.object(
            api.parsing_service,
            "parse_product",
            new=AsyncMock(side_effect=RuntimeError("Crawler blew up")),
        ):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert "Crawler blew up" in body["error"]

    def test_error_response_has_null_data_field(self, client):
        with patch.object(
            api.parsing_service,
            "parse_product",
            new=AsyncMock(side_effect=ValueError("bad data")),
        ):
            r = client.post(
                "/parse",
                json={"url": "https://ozon.ru/product/test"},
                headers=_AUTH_HEADERS,
            )
        assert r.json()["data"] is None


# ---------------------------------------------------------------------------
# GET /favicon.ico
# ---------------------------------------------------------------------------

class TestFavicon:
    def test_serves_svg(self, client):
        r = client.get("/favicon.ico")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/svg+xml"


# ---------------------------------------------------------------------------
# ParseRequest model tests (directly, without HTTP layer)
# ---------------------------------------------------------------------------

class TestParseRequestModel:
    def test_accepts_supported_wildberries_url(self):
        from models.product import ParseRequest
        req = ParseRequest(url="https://www.wildberries.ru/catalog/17386348/detail.aspx")
        assert req.url is not None

    def test_accepts_supported_ozon_url(self):
        from models.product import ParseRequest
        req = ParseRequest(url="https://ozon.ru/product/test-item")
        assert req.url is not None

    def test_accepts_supported_yandex_market_url(self):
        from models.product import ParseRequest
        req = ParseRequest(url="https://market.yandex.ru/card/item/123")
        assert req.url is not None

    def test_accepts_supported_megamarket_url(self):
        from models.product import ParseRequest
        req = ParseRequest(url="https://megamarket.ru/catalog/details/item-123")
        assert req.url is not None

    def test_rejects_unsupported_amazon_url(self):
        from pydantic import ValidationError

        from models.product import ParseRequest
        with pytest.raises(ValidationError):
            ParseRequest(url="https://amazon.com/dp/B08N5WRWNW")

    def test_rejects_completely_invalid_domain(self):
        from pydantic import ValidationError

        from models.product import ParseRequest
        with pytest.raises(ValidationError):
            ParseRequest(url="https://notamarketplace.example.com/product/1")

    def test_rejects_non_url_string(self):
        from pydantic import ValidationError

        from models.product import ParseRequest
        with pytest.raises(ValidationError):
            ParseRequest(url="this-is-not-a-url")
