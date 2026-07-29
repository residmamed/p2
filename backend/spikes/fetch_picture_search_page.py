import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402


async def main():
    client = ZyteClient()
    result = await client.extract(
        "https://www.alibaba.com/picture/search.htm", browser_html=True
    )
    html = result.get("browserHtml", "")
    out_dir = Path(__file__).parent
    out_dir.mkdir(exist_ok=True)
    (out_dir / "picture_search.html").write_text(html)
    meta = {k: v for k, v in result.items() if k != "browserHtml"}
    print(json.dumps(meta, indent=2)[:2000])
    print(f"\nHTML length: {len(html)} chars, saved to spikes/picture_search.html")


if __name__ == "__main__":
    asyncio.run(main())
