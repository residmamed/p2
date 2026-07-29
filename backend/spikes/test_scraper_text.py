import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scrapers.alibaba import AlibabaScraper  # noqa: E402


async def main():
    scraper = AlibabaScraper()
    products, warnings = await scraper.search_by_text("phone case")
    print("products:", len(products))
    print("warnings:", warnings)
    if products:
        p = products[0]
        print(p.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
