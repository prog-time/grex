import asyncio
import pytest
from decimal import Decimal

from models.product import (
    BatchParseRequest,
    BatchItemResult,
    BatchParseResponse,
)
from services.parsing import ParsingService


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------

class TestBatchParseRequest:
    def test_valid_request(self):
        req = BatchParseRequest(
            urls=["https://ozon.ru/product/test-123"],
            concurrency=5,
        )
        assert len(req.urls) == 1
        assert req.concurrency == 5

    def test_default_concurrency(self):
        req = BatchParseRequest(urls=["https://wildberries.ru/catalog/1/detail.aspx"])
        assert req.concurrency == 10

    def test_rejects_empty_urls(self):
        with pytest.raises(Exception):
            BatchParseRequest(urls=[])

    def test_rejects_more_than_100_urls(self):
        urls = ["https://ozon.ru/product/x"] * 101
        with pytest.raises(Exception):
            BatchParseRequest(urls=urls)

    def test_rejects_unsupported_domain(self):
        with pytest.raises(Exception):
            BatchParseRequest(urls=["https://amazon.com/dp/B08N5WRWNW"])

    def test_concurrency_lower_bound(self):
        with pytest.raises(Exception):
            BatchParseRequest(urls=["https://ozon.ru/product/test"], concurrency=0)

    def test_concurrency_upper_bound(self):
        with pytest.raises(Exception):
            BatchParseRequest(urls=["https://ozon.ru/product/test"], concurrency=51)

    def test_all_supported_domains_accepted(self):
        urls = [
            "https://wildberries.ru/catalog/1/detail.aspx",
            "https://wildberries.by/catalog/1/detail.aspx",
            "https://wildberries.kz/catalog/1/detail.aspx",
            "https://ozon.ru/product/test",
            "https://ozon.by/product/test",
            "https://ozon.kz/product/test",
            "https://market.yandex.ru/card/test/123",
            "https://market.yandex.by/card/test/123",
            "https://megamarket.ru/catalog/details/test",
        ]
        req = BatchParseRequest(urls=urls)
        assert len(req.urls) == 9


class TestBatchItemResult:
    def test_success_result(self):
        result = BatchItemResult(
            url="https://ozon.ru/product/test",
            success=True,
            data={"title": "Product"},
            marketplace="ozon",
            processing_time=0.5,
        )
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        result = BatchItemResult(
            url="https://ozon.ru/product/test",
            success=False,
            error="Connection error",
        )
        assert result.success is False
        assert result.data is None
        assert result.marketplace is None


class TestBatchParseResponse:
    def test_response_fields(self):
        results = [
            BatchItemResult(url="https://ozon.ru/product/1", success=True),
            BatchItemResult(url="https://ozon.ru/product/2", success=False, error="err"),
        ]
        resp = BatchParseResponse(
            total=2,
            succeeded=1,
            failed=1,
            processing_time=1.23,
            results=results,
        )
        assert resp.total == 2
        assert resp.succeeded == 1
        assert resp.failed == 1


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

def _make_product_card(url: str):
    from models.product import ProductCard
    return ProductCard(
        title="Test Product",
        sku="SKU123",
        original_price=Decimal("999.99"),
        current_price=Decimal("799.99"),
        images=["https://img.example.com/1.jpg"],
        source_url=url,
    )


@pytest.mark.asyncio
async def test_parse_products_batch_success():
    service = ParsingService()

    async def fake_parse(url: str):
        return _make_product_card(url)

    service.parse_product = fake_parse

    urls = [
        "https://ozon.ru/product/test-1",
        "https://wildberries.ru/catalog/2/detail.aspx",
    ]
    results = await service.parse_products_batch(urls, concurrency=2)

    assert len(results) == 2
    assert all(r.success for r in results)
    assert results[0].data["title"] == "Test Product"


@pytest.mark.asyncio
async def test_parse_products_batch_captures_exceptions():
    service = ParsingService()

    async def failing_parse(url: str):
        raise RuntimeError("Network timeout")

    service.parse_product = failing_parse

    urls = ["https://ozon.ru/product/fail-1"]
    results = await service.parse_products_batch(urls, concurrency=1)

    assert len(results) == 1
    assert results[0].success is False
    assert "Network timeout" in results[0].error


@pytest.mark.asyncio
async def test_parse_products_batch_mixed_results():
    service = ParsingService()
    call_count = {"n": 0}

    async def mixed_parse(url: str):
        call_count["n"] += 1
        if "fail" in url:
            raise ValueError("Bad URL")
        return _make_product_card(url)

    service.parse_product = mixed_parse

    urls = [
        "https://ozon.ru/product/ok",
        "https://ozon.ru/product/fail",
    ]
    results = await service.parse_products_batch(urls, concurrency=2)

    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    assert len(succeeded) == 1
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_parse_products_batch_concurrency_respected():
    service = ParsingService()
    active = {"count": 0, "max": 0}

    async def slow_parse(url: str):
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        await asyncio.sleep(0.05)
        active["count"] -= 1
        return _make_product_card(url)

    service.parse_product = slow_parse

    urls = ["https://ozon.ru/product/x"] * 10
    await service.parse_products_batch(urls, concurrency=3)

    assert active["max"] <= 3


@pytest.mark.asyncio
async def test_parse_products_batch_cookie_lock_initialized():
    service = ParsingService()
    assert "ozon" in service._cookie_locks
    assert "megamarket" in service._cookie_locks
    assert isinstance(service._cookie_locks["ozon"], asyncio.Lock)
    assert isinstance(service._cookie_locks["megamarket"], asyncio.Lock)


@pytest.mark.asyncio
async def test_parse_products_batch_returns_marketplace():
    service = ParsingService()

    async def fake_parse(url: str):
        return _make_product_card(url)

    service.parse_product = fake_parse

    urls = ["https://ozon.ru/product/test"]
    results = await service.parse_products_batch(urls)

    assert results[0].marketplace == "ozon"


@pytest.mark.asyncio
async def test_parse_products_batch_returns_processing_time():
    service = ParsingService()

    async def fake_parse(url: str):
        return _make_product_card(url)

    service.parse_product = fake_parse

    results = await service.parse_products_batch(["https://ozon.ru/product/t"])
    assert results[0].processing_time is not None
    assert results[0].processing_time >= 0
