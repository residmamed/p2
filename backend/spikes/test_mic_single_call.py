import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scrapers.made_in_china import MadeInChinaScraper  # noqa: E402

IMAGE_PATH = Path(__file__).parent / "test_image.jpg"


async def main():
    scraper = MadeInChinaScraper()
    image_bytes = IMAGE_PATH.read_bytes()
    url = await scraper._upload_image_and_get_results_url(image_bytes)
    print("RESULT URL:", url)


if __name__ == "__main__":
    asyncio.run(main())
