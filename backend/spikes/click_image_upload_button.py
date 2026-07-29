import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402


async def main():
    client = ZyteClient()
    actions = [
        {
            "action": "click",
            "selector": {"type": "css", "value": "[data-search='switch-image-upload']"},
        },
        {"action": "waitForTimeout", "timeout": 3},
    ]
    result = await client.extract(
        "https://www.alibaba.com/picture/search.htm",
        browser_html=True,
        actions=actions,
    )
    html = result.get("browserHtml", "")
    out_dir = Path(__file__).parent
    (out_dir / "after_click.html").write_text(html)
    action_results = result.get("actions", [])
    print(json.dumps(action_results, indent=2)[:3000])
    print(f"\nHTML length: {len(html)} chars, saved to spikes/after_click.html")


if __name__ == "__main__":
    asyncio.run(main())
