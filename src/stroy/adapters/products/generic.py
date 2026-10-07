"""Generic HTML product-page extractor (R3).

Strategy, in decreasing order of trust:

1. JSON-LD ``schema.org/Product`` blocks — machine-readable structured data:
   name, brand, model, offers.price/priceCurrency, image, and dimensions
   (width/depth/height as QuantitativeValue with an explicit unitCode).
   Dimensions come ONLY from these structured fields — never from free-text
   regex parsing, which is too unreliable to place furniture with.
2. OpenGraph fallback — og:title / og:image.
3. ``<meta name="description">`` — kept as a low-confidence material
   descriptor.

Confidence: structured dimensions found → 0.9 (high); JSON-LD facts (no
dims) → 0.7 (medium-high); OG/meta only → 0.3 (low); nothing → 0.0. A page
with no recognizable product data still yields an (empty, partial) candidate
— extraction failure to find facts is not an error.

All I/O goes through :func:`stroy.net.guard.fetch_guarded` (SSRF guard); the
``fetch`` callable is injectable for tests.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

from stroy.domain.products import ProductCandidateFacts
from stroy.net.guard import GuardedFetch, fetch_guarded

__all__ = ["FetchHtml", "GenericHtmlExtractor"]

FetchHtml = Callable[[str], Awaitable[GuardedFetch]]

# UN/CEFACT unit codes we are willing to convert to millimetres. Anything
# else is ignored rather than guessed.
_UNIT_TO_MM: dict[str, float] = {
    "MM": 1.0,
    "CMT": 10.0,
    "MTR": 1000.0,
    "IN": 25.4,   # common informal code for inches
    "FOT": 304.8,  # feet
}


class _HeadParser(HTMLParser):
    """Collect meta tags and JSON-LD script bodies (stdlib; no extra deps)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld_blocks: list[str] = []
        self._jsonld_buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if key and content is not None:
                self.meta.setdefault(key.lower(), content.strip())
        elif tag.lower() == "script":
            script_type = attributes.get("type", "").strip().lower()
            if script_type == "application/ld+json":
                self._jsonld_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._jsonld_buffer is not None:
            self.jsonld_blocks.append("".join(self._jsonld_buffer))
            self._jsonld_buffer = None

    def handle_data(self, data: str) -> None:
        if self._jsonld_buffer is not None:
            self._jsonld_buffer.append(data)


def _iter_product_nodes(node: Any):
    """Yield every dict that looks like a schema.org/Product node."""
    if isinstance(node, dict):
        raw_type = node.get("@type")
        types = [raw_type] if isinstance(raw_type, str) else raw_type or []
        if any(str(item).strip().lower() == "product" for item in types):
            yield node
        for value in node.values():
            yield from _iter_product_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_product_nodes(item)


def _first_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "value", "url", "contentUrl"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    if isinstance(value, list):
        for item in value:
            text = _first_str(item)
            if text:
                return text
    return None


def _as_price(value: Any) -> float | None:
    """Machine-readable price only: numbers or plain decimal strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, dict):
        for key in ("price", "value"):
            if key in value:
                return _as_price(value[key])
    return None


def _as_mm(value: Any) -> float | None:
    """QuantitativeValue → millimetres; requires an explicit known unit."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # A bare number carries no unit — ambiguous, do not guess.
        return None
    if not isinstance(value, dict):
        return None
    raw_value = value.get("value", value.get("minValue"))
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
        return None
    try:
        amount = float(Decimal(str(raw_value)))
    except (InvalidOperation, ValueError):
        return None
    unit = str(value.get("unitCode", "")).strip().upper()
    factor = _UNIT_TO_MM.get(unit)
    if factor is None:
        return None
    return amount * factor


def _first_offer(offers: Any) -> dict[str, Any] | None:
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        for item in offers:
            if isinstance(item, dict):
                return item
    return None


class GenericHtmlExtractor:
    """schema.org/Product JSON-LD + OpenGraph fallback extractor."""

    def __init__(self, *, fetch: FetchHtml | None = None) -> None:
        self._fetch = fetch or self._default_fetch

    @staticmethod
    async def _default_fetch(url: str) -> GuardedFetch:
        return await fetch_guarded(url, expect="html")

    async def extract(self, url: str) -> ProductCandidateFacts:
        page = await self._fetch(url)
        if not 200 <= page.status < 300:
            # Non-2xx pages (404/410/anti-bot walls) carry no product data
            # worth parsing: return an empty, partial result.
            return ProductCandidateFacts(extraction_confidence=0.0)

        parser = _HeadParser()
        try:
            parser.feed(page.body.decode("utf-8", errors="replace"))
            parser.close()
        except Exception:  # noqa: BLE001 - malformed markup must not 500
            return ProductCandidateFacts(extraction_confidence=0.0)

        facts = ProductCandidateFacts(extraction_confidence=0.0)
        saw_jsonld_fact = False
        saw_dimension = False

        for block in parser.jsonld_blocks:
            try:
                parsed = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue
            for node in _iter_product_nodes(parsed):
                if facts.title is None:
                    facts.title = _first_str(node.get("name"))
                if facts.brand is None:
                    facts.brand = _first_str(node.get("brand"))
                if facts.model is None:
                    facts.model = _first_str(node.get("model"))
                if facts.price is None or facts.currency is None:
                    offer = _first_offer(node.get("offers"))
                    if offer is not None:
                        if facts.price is None:
                            facts.price = _as_price(offer.get("price"))
                        if facts.currency is None:
                            currency = _first_str(offer.get("priceCurrency"))
                            facts.currency = (
                                currency.upper() if currency else None
                            )
                if facts.color is None:
                    facts.color = _first_str(node.get("color"))
                if facts.material is None:
                    facts.material = _first_str(node.get("material"))
                for target, key in (
                    ("width_mm", "width"),
                    ("depth_mm", "depth"),
                    ("height_mm", "height"),
                ):
                    if getattr(facts, target) is None:
                        mm = _as_mm(node.get(key))
                        if mm is not None:
                            setattr(facts, target, mm)
                            saw_dimension = True
                if facts.preview_image_url is None:
                    image = _first_str(node.get("image"))
                    if image:
                        facts.preview_image_url = urljoin(url, image)
                if (
                    facts.title
                    or facts.brand
                    or facts.model
                    or facts.price is not None
                    or facts.color
                    or facts.material
                ):
                    saw_jsonld_fact = True

        if facts.title is None:
            og_title = parser.meta.get("og:title")
            if og_title:
                facts.title = og_title
        if facts.preview_image_url is None:
            og_image = parser.meta.get("og:image")
            if og_image:
                facts.preview_image_url = urljoin(url, og_image.strip())
        if facts.material is None:
            description = parser.meta.get("description")
            if description:
                # Free-text page description: usable as a vague descriptor
                # only — never as placement geometry.
                facts.material = description

        if saw_dimension:
            facts.extraction_confidence = 0.9
        elif saw_jsonld_fact:
            facts.extraction_confidence = 0.7
        elif facts.title or facts.preview_image_url or facts.material:
            facts.extraction_confidence = 0.3
        else:
            facts.extraction_confidence = 0.0
        return facts
