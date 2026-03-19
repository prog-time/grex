"""Per-marketplace field definitions.

Each entry describes which ProductCard fields the crawler can extract
and where the data comes from (for display in the admin UI).
The actual extraction logic lives in the crawler classes — this module
is the single source of truth for *what is available* per marketplace.
"""
from __future__ import annotations

from typing import TypedDict


class FieldMeta(TypedDict):
    label: str        # Human-readable field label
    description: str  # Where the data is extracted from


# marketplace_name → {field_name → FieldMeta}
MARKETPLACE_FIELD_CONFIGS: dict[str, dict[str, FieldMeta]] = {
    "ozon": {
        "title": {
            "label": "Название",
            "description": "widgetStates → webProductHeading → title",
        },
        "sku": {
            "label": "Артикул",
            "description": "widgetStates → webGallery → sku",
        },
        "original_price": {
            "label": "Цена до скидки",
            "description": "widgetStates → webPrice → originalPrice",
        },
        "current_price": {
            "label": "Текущая цена",
            "description": "widgetStates → webPrice → price",
        },
        "images": {
            "label": "Фотографии",
            "description": "widgetStates → webGallery → images[].src",
        },
    },
    "wildberries": {
        "title": {
            "label": "Название",
            "description": "card.json → imt_name",
        },
        "sku": {
            "label": "Артикул",
            "description": "URL: /catalog/{sku}/detail.aspx",
        },
        "original_price": {
            "label": "Цена до скидки",
            "description": "cards/v3/detail → products[0].sizes[0].price.basic ÷ 100",
        },
        "current_price": {
            "label": "Текущая цена",
            "description": "cards/v3/detail → products[0].sizes[0].price.product ÷ 100",
        },
        "images": {
            "label": "Фотографии",
            "description": "mediabasket → /vol.../part.../{sku}/images/big/{n}.webp",
        },
    },
    "yandex_market": {
        "title": {
            "label": "Название",
            "description": "HTML → h1[data-auto=productCardTitle]",
        },
        "sku": {
            "label": "Артикул",
            "description": "URL: последний сегмент пути /card/.../{sku}",
        },
        "original_price": {
            "label": "Цена до скидки",
            "description": "HTML → span[data-auto=snippet-price-old]",
        },
        "current_price": {
            "label": "Текущая цена",
            "description": "HTML → span[data-auto=snippet-price-current]",
        },
        "images": {
            "label": "Фотографии",
            "description": "HTML → @card/ShortUrl → collections.mediaItem (origUrl)",
        },
    },
    "megamarket": {
        "title": {
            "label": "Название",
            "description": "productCardMainInfo API → goods.title",
        },
        "sku": {
            "label": "Артикул",
            "description": "URL: предпоследний сегмент пути (...-{sku}/)",
        },
        "current_price": {
            "label": "Текущая цена",
            "description": "productOffers API → offers[0].finalPrice",
        },
        "images": {
            "label": "Фотографии",
            "description": "productCardMainInfo API → goods.images[]",
        },
    },
}


def get_fields(marketplace: str) -> dict[str, FieldMeta]:
    """Return field definitions for *marketplace*, or empty dict if unknown."""
    return MARKETPLACE_FIELD_CONFIGS.get(marketplace, {})


def get_all_marketplace_names() -> list[str]:
    return list(MARKETPLACE_FIELD_CONFIGS.keys())
