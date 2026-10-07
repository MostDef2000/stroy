"""GenericHtmlExtractor tests (R3): canned HTML, guard I/O mocked at the seam."""

from __future__ import annotations

import pytest

from stroy.adapters.products.generic import GenericHtmlExtractor
from stroy.domain.products import ProductCandidateFacts
from stroy.net.guard import GuardedFetch

URL = "https://shop.example.com/product/sofa-1"


def make_fetch(
    html: str | None,
    *,
    status: int = 200,
    content_type: str | None = "text/html; charset=utf-8",
):
    async def fetch(url: str) -> GuardedFetch:
        return GuardedFetch(
            url=url,
            status=status,
            content_type=content_type,
            body=(html or "").encode("utf-8"),
            redirect_chain=[],
        )

    return fetch


FULL_JSONLD = """<!DOCTYPE html>
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product",
 "name": "Диван Скандинавия",
 "brand": {"@type": "Brand", "name": "HomeFlat"},
 "model": "SK-100",
 "offers": {"@type": "Offer", "price": "1299.90", "priceCurrency": "rub"},
 "image": "https://cdn.example.com/sofa.jpg",
 "width": {"@type": "QuantitativeValue", "value": 2200, "unitCode": "MM"},
 "depth": {"@type": "QuantitativeValue", "value": 0.95, "unitCode": "MTR"},
 "height": {"@type": "QuantitativeValue", "value": 85, "unitCode": "CMT"},
 "material": "массив дуба",
 "color": "beige"}
</script>
</head><body><p>shop page</p></body></html>"""


async def test_full_jsonld_product_extracts_everything() -> None:
    extractor = GenericHtmlExtractor(fetch=make_fetch(FULL_JSONLD))
    facts = await extractor.extract(URL)
    assert facts.title == "Диван Скандинавия"
    assert facts.brand == "HomeFlat"
    assert facts.model == "SK-100"
    assert facts.price == 1299.90
    assert facts.currency == "RUB"  # normalised to upper case
    # Structured dimensions only, with unit conversion to millimetres.
    assert facts.width_mm == 2200.0
    assert facts.depth_mm == 950.0  # 0.95 MTR
    assert facts.height_mm == 850.0  # 85 CMT
    assert facts.material == "массив дуба"
    assert facts.color == "beige"
    assert facts.preview_image_url == "https://cdn.example.com/sofa.jpg"
    assert facts.extraction_confidence == 0.9  # structured dims = high


async def test_price_without_dimensions_is_medium_high_confidence() -> None:
    html = """<html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Кресло", "offers": {"price": 99.5,
     "priceCurrency": "EUR"}, "brand": "SitWell"}
    </script></head><body></body></html>"""
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    assert facts.title == "Кресло"
    assert facts.brand == "SitWell"
    assert facts.price == 99.5
    assert facts.currency == "EUR"
    assert facts.width_mm is None and facts.depth_mm is None
    assert facts.extraction_confidence == 0.7


async def test_open_graph_fallback_is_low_confidence() -> None:
    html = """<html><head>
    <meta property="og:title" content="Стол обеденный">
    <meta property="og:image" content="/img/table.jpg">
    <meta name="description" content="Стол из ясеня, покрытие масло">
    </head><body></body></html>"""
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    assert facts.title == "Стол обеденный"
    assert facts.preview_image_url == "https://shop.example.com/img/table.jpg"
    # Meta description becomes a low-confidence material descriptor.
    assert facts.material == "Стол из ясеня, покрытие масло"
    assert facts.brand is None and facts.price is None
    assert facts.width_mm is None
    assert facts.extraction_confidence == 0.3


async def test_garbage_page_yields_partial_not_failure() -> None:
    html = "<html><head><title>404 — not a shop</title></head><body>oops</body></html>"
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    assert isinstance(facts, ProductCandidateFacts)
    assert facts.title is None
    assert facts.brand is None
    assert facts.price is None
    assert facts.width_mm is None
    assert facts.preview_image_url is None
    assert facts.material is None
    assert facts.extraction_confidence == 0.0


async def test_dimensions_require_known_explicit_unit() -> None:
    html = """<html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Полка",
     "width": {"value": 100},
     "depth": {"value": 40, "unitCode": "FURLONG"},
     "height": {"value": 80, "unitCode": "IN"}}
    </script></head><body></body></html>"""
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    # Bare number without unitCode is ambiguous — never guessed.
    assert facts.width_mm is None
    # Unsupported unit is ignored, not converted.
    assert facts.depth_mm is None
    assert facts.height_mm == pytest.approx(2032.0)  # 80 IN
    # A usable dimension was still found → high confidence.
    assert facts.extraction_confidence == 0.9


async def test_offers_list_and_graph_array_supported() -> None:
    html = """<html><head>
    <script type="application/ld+json">
    {"@graph": [
       {"@type": "WebPage", "name": "page"},
       {"@type": "Product", "name": "Тумба",
        "offers": [{"@type": "Offer", "price": "749.99", "priceCurrency": "USD"},
                   {"@type": "Offer", "priceCurrency": "USD"}],
        "image": [{"url": "https://cdn.example.com/1.jpg"}]}
    ]}
    </script></head><body></body></html>"""
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    assert facts.title == "Тумба"
    assert facts.price == 749.99
    assert facts.currency == "USD"
    assert facts.preview_image_url == "https://cdn.example.com/1.jpg"


async def test_non_2xx_page_returns_empty_facts() -> None:
    extractor = GenericHtmlExtractor(
        fetch=make_fetch("<html>error</html>", status=404)
    )
    facts = await extractor.extract(URL)
    assert facts.extraction_confidence == 0.0
    assert facts.title is None


async def test_broken_jsonld_is_tolerated() -> None:
    html = """<html><head>
    <script type="application/ld+json">{"@type": "Product", "name": broken</script>
    <meta property="og:title" content="Fallback">
    </head><body></body></html>"""
    facts = await GenericHtmlExtractor(fetch=make_fetch(html)).extract(URL)
    assert facts.title == "Fallback"
    assert facts.extraction_confidence == 0.3
