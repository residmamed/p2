import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402


async def main():
    client = ZyteClient()
    query = "wireless earbuds"
    url = f"https://www.alibaba.com/trade/search?SearchText={quote(query)}"
    result = await client.extract(url, browser_html=True)
    html = result.get("browserHtml", "")
    out_dir = Path(__file__).parent
    (out_dir / "text_search.html").write_text(html)
    print("statusCode:", result.get("statusCode"))
    print("HTML length:", len(html))
    import re

    title_match = re.search(r"<title>(.*?)</title>", html)
    print("TITLE:", title_match.group(1) if title_match else "NOT FOUND")


if __name__ == "__main__":
    asyncio.run(main())
