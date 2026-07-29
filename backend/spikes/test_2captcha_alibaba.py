import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

WEBSITE_URL = "https://www.alibaba.com/picture/search.htm"

TASK = {
    "type": "AlibabaTaskProxyless",
    "websiteUrl": WEBSITE_URL,
    "sceneId": "register",
    "prefix": "cf",
    "userAgent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


async def main():
    payload = {"clientKey": settings.twocaptcha_api_key, "task": TASK}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.2captcha.com/createTask", json=payload)
    print("createTask status:", resp.status_code)
    print("createTask body:", resp.text[:1000])

    data = resp.json()
    task_id = data.get("taskId")
    if not task_id:
        print("No taskId — stopping here.")
        return

    print(f"\nPolling getTaskResult for taskId={task_id} ...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(20):
            await asyncio.sleep(5)
            result_resp = await client.post(
                "https://api.2captcha.com/getTaskResult",
                json={"clientKey": settings.twocaptcha_api_key, "taskId": task_id},
            )
            result = result_resp.json()
            status = result.get("status")
            print(f"  poll {i+1}: status={status} body={result_resp.text[:500]}")
            if status == "ready":
                print("\nSOLVED:", result.get("solution"))
                return
            if status not in ("processing", "idle", None):
                print("\nUnexpected status, stopping.")
                return

    print("Timed out waiting for solution.")


if __name__ == "__main__":
    asyncio.run(main())
