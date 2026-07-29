# Zyte Lens Bridge (Chrome extension)

Runs a Google Lens reverse-image search in a real, ordinary Chrome tab —
not an automated/proxied browser — and hands the results back to the app
running at `localhost:5173`. This exists because server-side automation
(Apify actors, a Zyte-proxied headless browser) reliably got blocked by
Google's bot detection; a real extension in your real browser doesn't carry
those automation signals.

## Install (one-time, local dev only)

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `extension/` folder.
4. Confirm it loaded with the exact ID `eljofghojhhdoajgdbnibkifalnjjacd` —
   the frontend is hardcoded to talk to that ID specifically (see
   `frontend/src/api.js`). If Chrome assigns a different ID, something's off
   with the `key` field in `manifest.json` — don't regenerate it, that's what
   pins the ID.
5. Reload the Trending page at `localhost:5173`. Searches will now also try
   the extension automatically if it's installed and enabled; if it's not
   installed, the app just skips this source silently.

## How it works

1. The web page calls `chrome.runtime.sendMessage(EXTENSION_ID, {type: "SEARCH_LENS", imageDataUrl})`.
2. The extension's background service worker opens a background tab to
   `google.com/imghp`, clicks the camera/image-search icon, and injects the
   cropped image into the file input via a `DataTransfer` (no OS file-picker
   dialog needed).
3. It waits for navigation to the results page, then scrapes it and returns
   structured results (title/link pairs) back to the page.
4. The tab is closed automatically.

## Known limitation — the results scraper is best-effort

Every automated attempt at this flow (Apify, direct Playwright) got blocked
*before* reaching a real results page, so the selectors in
`scrapeResultsInPage()` (in `background.js`) are educated guesses based on
Google's general search-result DOM patterns, not verified against a real
Lens results page. If a real search comes back with an empty or wrong-looking
`results` array, check the browser's extension service-worker console
(`chrome://extensions` → this extension → "service worker" → Inspect) and the
opened tab's DOM, and we'll adjust the selectors together.
