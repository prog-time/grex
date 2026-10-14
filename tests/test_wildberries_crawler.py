"""Wildberries: prices and seller links through the internal API with the anti-bot token.

The network and the browser are replaced by fakes; nothing reaches WB.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from crawlers.wildberries import WildberriesCrawler

SESSION = {"headers": {"x-spa-version": "1"}, "cookies": {"x_wbaas_token": "t"}, "obtained_at": 0}


class FakeResponse:
    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}

    def json(self) -> dict:
        return self._data


@pytest.fixture
def crawler(tmp_path):
    WildberriesCrawler._api_session = None
    with patch.object(WildberriesCrawler, "api_session_file_path", str(tmp_path / "wb_api_session.json")):
        c = WildberriesCrawler()
        c.proxy = None
        yield c
    WildberriesCrawler._api_session = None


def _card(sku: int, basic: int | None, product: int | None) -> dict:
    price = {"basic": basic, "product": product} if product else None
    return {"id": sku, "sizes": [{"price": price}]}


class TestInternalPrices:
    async def test_prices_in_rubles(self, crawler):
        with patch.object(crawler, "_internal_get", new=AsyncMock(return_value={"products": [_card(5, 439600, 179800)]})):
            assert await crawler._fetch_prices_internal("5") == (Decimal("4396"), Decimal("1798"))

    @pytest.mark.parametrize("data", [None, {"products": []}, {"products": [_card(5, None, None)]},
                                      {"products": [_card(6, 100, 90)]}])
    async def test_no_price(self, crawler, data):
        with patch.object(crawler, "_internal_get", new=AsyncMock(return_value=data)):
            assert await crawler._fetch_prices_internal("5") == (Decimal(0), Decimal(0))


class TestInternalGet:
    async def test_uses_cached_session_without_browser(self, crawler):
        WildberriesCrawler._api_session = SESSION
        with patch.object(crawler.session, "get", new=AsyncMock(return_value=FakeResponse(200, {"ok": 1}))) as get, \
                patch.object(crawler, "_refresh_api_session", new=AsyncMock()) as refresh:
            assert await crawler._internal_get("x") == {"ok": 1}
        refresh.assert_not_awaited()
        assert get.await_args.kwargs["cookies"] == SESSION["cookies"]

    @pytest.mark.parametrize("status", [403, 498])
    async def test_refreshes_token_once_on_rejection(self, crawler, status):
        WildberriesCrawler._api_session = SESSION
        fresh = {**SESSION, "cookies": {"x_wbaas_token": "new"}}
        responses = [FakeResponse(status), FakeResponse(200, {"ok": 1})]
        with patch.object(crawler.session, "get", new=AsyncMock(side_effect=responses)) as get, \
                patch.object(crawler, "_refresh_api_session", new=AsyncMock(return_value=fresh)) as refresh:
            assert await crawler._internal_get("x") == {"ok": 1}
        refresh.assert_awaited_once_with(SESSION)
        assert get.await_args.kwargs["cookies"] == fresh["cookies"]

    async def test_gives_up_after_second_rejection(self, crawler):
        WildberriesCrawler._api_session = SESSION
        with patch.object(crawler.session, "get", new=AsyncMock(return_value=FakeResponse(403))), \
                patch.object(crawler, "_refresh_api_session", new=AsyncMock(return_value=SESSION)) as refresh:
            assert await crawler._internal_get("x") is None
        refresh.assert_awaited_once()

    async def test_no_token_means_no_request(self, crawler):
        with patch.object(crawler, "_refresh_api_session", new=AsyncMock(return_value=None)), \
                patch.object(crawler.session, "get", new=AsyncMock()) as get:
            assert await crawler._internal_get("x") is None
        get.assert_not_awaited()


class TestApiSessionRefresh:
    async def test_saves_token_to_file_and_memory(self, crawler, tmp_path):
        result = {"success": True, "headers": SESSION["headers"], "cookies": SESSION["cookies"]}
        with patch.object(crawler, "_run_in_process", new=AsyncMock(return_value=result)):
            session = await crawler._refresh_api_session(None)
        assert session["cookies"] == SESSION["cookies"]
        assert WildberriesCrawler._api_session is session
        WildberriesCrawler._api_session = None
        assert crawler._load_api_session()["cookies"] == SESSION["cookies"]  # read back from the file

    async def test_concurrent_refresh_opens_one_browser(self, crawler):
        import asyncio
        result = {"success": True, "headers": SESSION["headers"], "cookies": SESSION["cookies"]}
        with patch.object(crawler, "_run_in_process", new=AsyncMock(return_value=result)) as run:
            await asyncio.gather(*[crawler._refresh_api_session(None) for _ in range(5)])
        run.assert_awaited_once()

    async def test_browser_failure(self, crawler):
        with patch.object(crawler, "_run_in_process", new=AsyncMock(return_value={"success": False, "error": "x"})):
            assert await crawler._refresh_api_session(None) is None


class TestSellerLinks:
    async def test_internal_catalog_pages(self, crawler):
        pages = [{"products": [{"id": 1}, {"id": 2}]}, {"products": [{"id": 3}]}, {"products": []}]
        with patch.object(crawler, "_internal_get", new=AsyncMock(side_effect=pages)), \
                patch.object(crawler, "get_proxy", new=AsyncMock(return_value=None)), \
                patch.object(crawler, "_get_links_via_browser", new=AsyncMock()) as browser:
            links = await crawler.get_product_links("https://www.wildberries.ru/seller/687676", max_pages=10)
        assert links == [f"https://www.wildberries.ru/catalog/{i}/detail.aspx" for i in (1, 2, 3)]
        browser.assert_not_awaited()
