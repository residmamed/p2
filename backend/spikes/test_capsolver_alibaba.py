import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

WEBSITE_URL = "https://www.alibaba.com/picture/search.htm"

# Best-effort param mapping from the live capture:
#   cf.aliyun.com/nocaptcha/initialize.jsonp?a=<NCAPPKEY>&t=<NCTOKENSTR>&scene=register&...
# 2captcha's AlibabaTaskProxyless wants: websiteUrl, sceneId, prefix (subdomain of captcha request URL).
CANDIDATES = [
    {
        "type": "AlibabaTaskProxyless",
        "websiteUrl": WEBSITE_URL,
        "sceneId": "register",
        "prefix": "cf",
    },
    {
        "type": "AlibabaTask",
        "websiteUrl": WEBSITE_URL,
        "sceneId": "register",
        "prefix": "cf",
    },
    {
        "type": "NoCaptchaTaskProxyless",
        "websiteUrl": WEBSITE_URL,
        "sceneId": "register",
        "prefix": "cf",
    },
]


async def try_task(task: dict):
    payload = {"clientKey": settings.capsolver_api_key, "task": task}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.capsolver.com/createTask", json=payload)
    print(f"\n--- type={task['type']} ---")
    print("HTTP status:", resp.status_code)
    print("body:", resp.text[:1000])


async def main():
    for task in CANDIDATES:
        await try_task(task)


if __name__ == "__main__":
    asyncio.run(main())
