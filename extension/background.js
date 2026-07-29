const SEARCH_TIMEOUT_MS = 45000;

chrome.runtime.onMessageExternal.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "SEARCH_LENS") return false;

  runLensSearch(message.imageDataUrl)
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }));

  return true; // keep the message channel open for the async sendResponse above
});

async function runLensSearch(imageDataUrl) {
  const tab = await chrome.tabs.create({ url: "https://www.google.com/imghp", active: false });
  const tabId = tab.id;

  try {
    await waitForTabComplete(tabId);

    await chrome.scripting.executeScript({
      target: { tabId },
      func: uploadImageInPage,
      args: [imageDataUrl],
    });

    await waitForUrlChange(tabId, "google.com/imghp", SEARCH_TIMEOUT_MS);
    await waitForTabComplete(tabId);
    await sleep(3000); // let client-side rendering on the results page settle

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: scrapeResultsInPage,
    });

    return result;
  } finally {
    chrome.tabs.remove(tabId).catch(() => {});
  }
}

function waitForTabComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Timed out waiting for tab to finish loading"));
    }, timeoutMs);

    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);

    chrome.tabs.get(tabId).then((t) => {
      if (t.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });
  });
}

function waitForUrlChange(tabId, mustNotContain, timeoutMs) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const interval = setInterval(async () => {
      try {
        const t = await chrome.tabs.get(tabId);
        if (t.url && !t.url.includes(mustNotContain)) {
          clearInterval(interval);
          resolve();
        } else if (Date.now() - start > timeoutMs) {
          clearInterval(interval);
          reject(new Error("Timed out waiting for navigation to the results page"));
        }
      } catch (e) {
        clearInterval(interval);
        reject(e);
      }
    }, 500);
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---- Injected into the Google tab via chrome.scripting.executeScript. ----
// Must be self-contained: no references to anything in this file's outer scope.

async function uploadImageInPage(imageDataUrl) {
  function firstMatch(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  const cameraIcon = firstMatch([
    "[aria-label='Search by image']",
    "[aria-label*='search by image' i]",
    "[aria-label*='image' i][role='button']",
  ]);
  if (!cameraIcon) throw new Error("Could not find the camera/image-search icon");
  cameraIcon.click();

  await new Promise((r) => setTimeout(r, 1200));

  let fileInput = document.querySelector("input[type=file]");
  let attempts = 0;
  while (!fileInput && attempts < 10) {
    await new Promise((r) => setTimeout(r, 500));
    fileInput = document.querySelector("input[type=file]");
    attempts++;
  }
  if (!fileInput) throw new Error("Could not find the file upload input after clicking the icon");

  const res = await fetch(imageDataUrl);
  const blob = await res.blob();
  const file = new File([blob], "crop.jpg", { type: blob.type || "image/jpeg" });

  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(file);
  fileInput.files = dataTransfer.files;
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  fileInput.dispatchEvent(new Event("input", { bubbles: true }));

  return true;
}

function scrapeResultsInPage() {
  // Best-effort: Google's reverse-image/Lens results DOM isn't publicly
  // documented and shifts over time. This grabs anything link-shaped that
  // looks like a result card, plus page title/URL/HTML length for
  // debugging when these selectors need adjusting against a real result.
  const results = [];
  const seen = new Set();

  const candidateSelectors = [
    "div[data-hveid] a[href^='http']",
    "a.wXeWr",
    "div.g a[href^='http']",
    "a[jsname][href^='http']",
  ];

  for (const sel of candidateSelectors) {
    document.querySelectorAll(sel).forEach((a) => {
      const href = a.href;
      if (!href || seen.has(href) || href.includes("google.com")) return;
      const title =
        a.querySelector("h3, [role='heading']")?.textContent?.trim() ||
        a.textContent?.trim()?.slice(0, 200) ||
        "";
      if (!title) return;
      seen.add(href);
      results.push({ title, link: href });
    });
  }

  return {
    url: location.href,
    title: document.title,
    results: results.slice(0, 30),
    htmlLength: document.documentElement.outerHTML.length,
  };
}
