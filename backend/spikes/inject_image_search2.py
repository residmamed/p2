import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402

IMAGE_PATH = Path(__file__).parent / "test_image.jpg"

INJECT_JS = """
() => {
  const b64 = "%s";
  const contentType = "image/jpeg";
  const byteChars = atob(b64);
  const byteNumbers = new Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
  const byteArray = new Uint8Array(byteNumbers);
  const file = new File([byteArray], "search.jpg", { type: contentType });
  const dt = new DataTransfer();
  dt.items.add(file);
  const candidates = document.querySelectorAll("input[type=file]");
  let debug = "NO_INPUT_" + candidates.length;
  if (candidates.length > 0) {
    const input = candidates[0];
    input.files = dt.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    debug = "DISPATCHED_files=" + input.files.length + "_name=" + (input.files[0] ? input.files[0].name : "none");
  }
  document.title = "DEBUG:" + debug;
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
        {"action": "waitForTimeout", "timeout": 5},
    ]
    result = await client.extract(
        "https://www.alibaba.com/picture/search.htm",
        browser_html=True,
        actions=actions,
        screenshot=True,
    )
    html = result.get("browserHtml", "")
    out_dir = Path(__file__).parent
    (out_dir / "after_inject2.html").write_text(html)

    screenshot_b64 = result.get("screenshot")
    if screenshot_b64:
        (out_dir / "after_inject2.png").write_bytes(base64.b64decode(screenshot_b64))

    print(json.dumps(result.get("actions", []), indent=2)[:5000])
    print(f"\nHTML length: {len(html)} chars")
    import re
    title_match = re.search(r"<title>(.*?)</title>", html)
    print("TITLE:", title_match.group(1) if title_match else "NOT FOUND")
    print("Screenshot saved:", bool(screenshot_b64))


if __name__ == "__main__":
    asyncio.run(main())
