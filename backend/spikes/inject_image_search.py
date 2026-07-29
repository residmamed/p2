import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402

IMAGE_PATH = Path(__file__).parent / "test_image.jpg"

INJECT_JS = """
async () => {
  const base64 = "%s";
  const contentType = "image/jpeg";
  const res = await fetch(`data:${contentType};base64,${base64}`);
  const blob = await res.blob();
  const file = new File([blob], "search.jpg", { type: contentType });
  const dt = new DataTransfer();
  dt.items.add(file);
  const input = document.querySelector("input[type=file].upload-file, input[name='image-search-upload']");
  if (!input) { return "NO_INPUT_FOUND"; }
  input.files = dt.files;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return "DISPATCHED";
}
"""


async def main():
    image_bytes = IMAGE_PATH.read_bytes()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    js = INJECT_JS % b64

    client = ZyteClient()
    actions = [
        {
            "action": "click",
            "selector": {"type": "css", "value": "[data-search='switch-image-upload']"},
        },
        {"action": "waitForTimeout", "timeout": 2},
        {"action": "evaluate", "source": js},
        {"action": "waitForTimeout", "timeout": 6},
    ]
    result = await client.extract(
        "https://www.alibaba.com/picture/search.htm",
        browser_html=True,
        actions=actions,
    )
    html = result.get("browserHtml", "")
    out_dir = Path(__file__).parent
    (out_dir / "after_inject.html").write_text(html)
    print(json.dumps(result.get("actions", []), indent=2)[:5000])
    print(f"\nHTML length: {len(html)} chars, saved to spikes/after_inject.html")


if __name__ == "__main__":
    asyncio.run(main())
