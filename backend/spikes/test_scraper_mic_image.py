import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scrapers.made_in_china import MadeInChinaScraper  # noqa: E402

IMAGE_PATH = Path(__file__).parent / "test_image.jpg"


async def main():
    scraper = MadeInChinaScraper()
    image_bytes = IMAGE_PATH.read_bytes()
    products, warnings = await scraper.search_by_image(image_bytes, "image/jpeg")
    print("products:", len(products))
    print("warnings:", warnings)
    if products:
        print(products[0].model_dump())


if __name__ == "__main__":
    asyncio.run(main())
