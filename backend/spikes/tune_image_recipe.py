"""Tune one site's image-upload recipe against the live site.

The recipes in app/scrapers/image_discovery.py are selector-based, so they rot
when a site redesigns and they can't be verified from fixtures. This runs one
recipe for real and dumps everything needed to fix it: a screenshot at each
stage, the final URL, and the page HTML.

    python -m spikes.tune_image_recipe 1688 path/to/product.jpg

Output lands in spikes/out/<site>/. Start with `1688` — its recipe is the one
marked UNVERIFIED.

If the camera button isn't found, open the screenshot, find the real selector,
and add it to that recipe's `open_upload` list (it tries them in order, so
adding rather than replacing keeps the old one working if the site A/Bs).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import browserbase_client as bb  # noqa: E402
from app.scrapers.image_discovery import RECIPES, VERIFIED, _dismiss, _open_upload_widget  # noqa: E402

OUT = Path(__file__).parent / "out"


async def main(site: str, image_path: str) -> None:
    recipe = RECIPES.get(site)
    if recipe is None:
        raise SystemExit(f"No recipe for {site!r}. Have: {', '.join(RECIPES)}")
    if not bb.is_configured():
        raise SystemExit("Set BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID in backend/.env")

    image_bytes = Path(image_path).read_bytes()
    out = OUT / site
    out.mkdir(parents=True, exist_ok=True)

    print(f"site={site}  provenance={VERIFIED.get(site, 'unknown')}")

    async with bb.remote_browser() as rb:
        print(f"session   https://browserbase.com/sessions/{rb.session_id}")
        page = rb.page

        await page.goto(recipe.entry_url, wait_until="domcontentloaded", timeout=45_000)
        await page.screenshot(path=out / "1-landed.png", full_page=False)
        print(f"landed    {page.url}\n          title={await page.title()!r}")

        await _dismiss(page, recipe.dismiss)
        opened = await _open_upload_widget(page, recipe.open_upload)
        await page.screenshot(path=out / "2-upload-open.png")
        print(f"camera    {'clicked' if opened else 'NOT FOUND — fix open_upload'}")

        inputs = await page.locator("input[type=file]").count()
        print(f"file inputs on page: {inputs}")
        if not inputs:
            (out / "page.html").write_text(await page.content())
            print(f"no file input — see {out/'page.html'} and 2-upload-open.png")
            return

        remote_path = await bb.upload_to_session(rb.session_id, image_bytes, f"query.{image_path.rsplit('.', 1)[-1]}")
        await bb.attach_file(rb, recipe.file_input, remote_path)
        print(f"attached  {remote_path}")

        for i in range(1, 13):
            await page.wait_for_timeout(2500)
            matched = recipe.results_pattern.search(page.url) if recipe.results_pattern else None
            print(f"  t+{i*2.5:>4.1f}s  {'MATCH ' if matched else '      '}{page.url[:110]}")
            if matched:
                break

        await page.screenshot(path=out / "3-results.png", full_page=True)
        (out / "page.html").write_text(await page.content())
        print(f"\nfinal URL {page.url}")
        print(f"artifacts {out}")
        print(
            "\nIf the URL matched, that pattern is what Zyte fetches next — "
            "paste it into a browser to confirm it renders results without a session."
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
