"""Scraper registry — one self-contained scraper module per shop."""
from ..models import Shop
from .aliexpress import AliExpressScraper
from .amazon import AmazonScraper
from .base import (
    OrderNotFound,
    ParseFailed,
    ScrapeError,
    Scraper,
    SessionExpired,
)
from .temu import TemuScraper

_SCRAPERS: dict[Shop, type[Scraper]] = {
    Shop.amazon: AmazonScraper,
    Shop.aliexpress: AliExpressScraper,
    Shop.temu: TemuScraper,
}


def get_scraper(shop: Shop) -> Scraper:
    return _SCRAPERS[shop]()


__all__ = [
    "get_scraper",
    "Scraper",
    "ScrapeError",
    "SessionExpired",
    "OrderNotFound",
    "ParseFailed",
]
