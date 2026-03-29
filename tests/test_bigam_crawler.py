"""Unit tests for BigamCrawler static parsing helpers.

Covers:
- JSON-LD extraction (_parse_jsonld)
- Old price extraction (_extract_old_price)
- Characteristics extraction (_extract_characteristics)
- SKU extraction from URL (_extract_sku_from_url)
- is_valid_url()

No network calls are made — all tests operate on synthetic HTML strings.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from crawlers.bigam import BigamCrawler


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_jsonld_html(product_data: dict) -> str:
    """Wrap *product_data* in a minimal HTML page with a JSON-LD script block."""
    blob = json.dumps(product_data, ensure_ascii=False)
    return (
        "<html><head>"
        f'<script type="application/ld+json">{blob}</script>'
        "</head><body></body></html>"
    )


# ---------------------------------------------------------------------------
# _parse_jsonld
# ---------------------------------------------------------------------------

class TestParseJsonld:
    def test_returns_product_block(self):
        data = {"@type": "Product", "name": "Тестовый товар", "sku": "ABC123"}
        html = _make_jsonld_html(data)
        result = BigamCrawler._parse_jsonld(html)
        assert result["name"] == "Тестовый товар"
        assert result["sku"] == "ABC123"

    def test_ignores_non_product_blocks(self):
        data = {"@type": "BreadcrumbList", "itemListElement": []}
        html = _make_jsonld_html(data)
        assert BigamCrawler._parse_jsonld(html) == {}

    def test_returns_empty_when_no_script(self):
        html = "<html><body><p>No script here</p></body></html>"
        assert BigamCrawler._parse_jsonld(html) == {}

    def test_returns_empty_on_invalid_json(self):
        html = '<html><head><script type="application/ld+json">{broken json</script></head></html>'
        assert BigamCrawler._parse_jsonld(html) == {}

    def test_picks_product_among_multiple_blocks(self):
        breadcrumb = json.dumps({"@type": "BreadcrumbList", "itemListElement": []})
        product = json.dumps({"@type": "Product", "name": "Мотоблок"})
        html = (
            "<html><head>"
            f'<script type="application/ld+json">{breadcrumb}</script>'
            f'<script type="application/ld+json">{product}</script>'
            "</head></html>"
        )
        result = BigamCrawler._parse_jsonld(html)
        assert result["name"] == "Мотоблок"


# ---------------------------------------------------------------------------
# _extract_old_price
# ---------------------------------------------------------------------------

class TestExtractOldPrice:
    def test_extracts_old_price_from_nuxt_state_colon(self):
        html = 'window.__NUXT__={priceCurrent:{current:44663,old:56120,discount:11457}}'
        result = BigamCrawler._extract_old_price(html, Decimal(44663))
        assert result == Decimal(56120)

    def test_extracts_old_price_from_fn_assignment(self):
        html = "fn.current=44663;fn.old=56120;fn.discount=11457;fn.retail=56120"
        result = BigamCrawler._extract_old_price(html, Decimal(44663))
        assert result == Decimal(56120)

    def test_extracts_retail_price_when_old_absent(self):
        html = "fn.current=10000;fn.retail=12500;"
        result = BigamCrawler._extract_old_price(html, Decimal(10000))
        assert result == Decimal(12500)

    def test_ignores_old_price_lower_than_current(self):
        # old < current should not be used
        html = "fn.current=50000;fn.old=30000;"
        result = BigamCrawler._extract_old_price(html, Decimal(50000))
        assert result == Decimal(0)

    def test_falls_back_to_visible_price_spans(self):
        html = "<p>44 663 ₽</p><p>56 120 ₽</p>"
        result = BigamCrawler._extract_old_price(html, Decimal(44663))
        assert result == Decimal(56120)

    def test_returns_zero_when_no_old_price(self):
        html = "<p>Товар в наличии.</p>"
        result = BigamCrawler._extract_old_price(html, Decimal(5000))
        assert result == Decimal(0)


# ---------------------------------------------------------------------------
# _extract_characteristics
# ---------------------------------------------------------------------------

class TestExtractCharacteristics:
    def test_extracts_string_values(self):
        html = '"Производитель":{"value":["Агат"],"description":"","important":true}'
        result = BigamCrawler._extract_characteristics(html)
        assert result.get("Производитель") == "Агат"

    def test_extracts_numeric_values(self):
        html = '"Мощность, л.с.":{"value":[7],"description":"","important":true}'
        result = BigamCrawler._extract_characteristics(html)
        assert result.get("Мощность, л.с.") == "7"

    def test_skips_internal_nuxt_keys(self):
        html = '"value":{"value":["ignored"]}'
        result = BigamCrawler._extract_characteristics(html)
        assert "value" not in result

    def test_skips_empty_value_arrays(self):
        html = '"Размер":{"value":[],"description":""}'
        result = BigamCrawler._extract_characteristics(html)
        assert "Размер" not in result

    def test_multiple_characteristics(self):
        html = (
            '"Производитель":{"value":["Агат"],"important":true},'
            '"Тип двигателя":{"value":["бензиновый"],"important":false},'
            '"Вес, кг":{"value":[96],"important":false}'
        )
        result = BigamCrawler._extract_characteristics(html)
        assert result.get("Производитель") == "Агат"
        assert result.get("Тип двигателя") == "бензиновый"
        assert result.get("Вес, кг") == "96"


# ---------------------------------------------------------------------------
# _extract_sku_from_url
# ---------------------------------------------------------------------------

class TestExtractSkuFromUrl:
    _LONG_URL = (
        "https://nizhniy-novgorod.bigam.ru/product/"
        "agat-l7-lyuks-motoblok-benzinovyi-7ls-2vpered-1nazad-60-80sm-2064429/"
    )

    @pytest.mark.parametrize("url, expected", [
        (
            _LONG_URL,
            "2064429",
        ),
        (
            "https://moskva.bigam.ru/product/some-product-1234567/",
            "1234567",
        ),
        (
            "https://bigam.ru/product/item-999/",
            "999",
        ),
    ])
    def test_extracts_numeric_id(self, url, expected):
        assert BigamCrawler._extract_sku_from_url(url) == expected

    def test_returns_none_for_non_numeric_slug(self):
        url = "https://bigam.ru/product/no-numbers-here/"
        assert BigamCrawler._extract_sku_from_url(url) is None


# ---------------------------------------------------------------------------
# is_valid_url
# ---------------------------------------------------------------------------

class TestIsValidUrl:
    @pytest.mark.parametrize("url", [
        "https://nizhniy-novgorod.bigam.ru/product/some-product-2064429/",
        "https://moskva.bigam.ru/catalog/motobloki/",
        "https://bigam.ru/product/test-123/",
        "https://BIGAM.RU/product/uppercase/",
    ])
    def test_returns_true_for_bigam_urls(self, url):
        crawler = BigamCrawler()
        assert crawler.is_valid_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://wildberries.ru/catalog/1/detail.aspx",
        "https://ozon.ru/product/some-item",
        "https://vseinstrumenti.ru/product/tool-123/",
        "https://amazon.com/dp/B08N5WRWNW",
    ])
    def test_returns_false_for_other_sites(self, url):
        crawler = BigamCrawler()
        assert crawler.is_valid_url(url) is False
