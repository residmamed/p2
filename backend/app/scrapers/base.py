from abc import ABC, abstractmethod

from ..models import Product


class Scraper(ABC):
    @abstractmethod
    async def search_by_text(self, query: str, page: int = 1) -> tuple[list[Product], list[str]]:
        """Return (results, warnings)."""

    @abstractmethod
    async def search_by_image(self, image_bytes: bytes, content_type: str) -> tuple[list[Product], list[str]]:
        """Return (results, warnings)."""
