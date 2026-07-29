"""Upgrade retail thumbnail URLs to full-resolution ones.

Every source hands back a thumbnail sized for its own grid, which looked blurry
once the cards render larger than the source's tile. Measured on a live search:

    Amazon    .../71xyz._AC_UL320_.jpg          320px
    IKEA      .../foo__123_pe456_s5.jpg?f=xxs   IKEA's smallest preset
    Walmart   .../bar.jpeg?odnHeight=576&...    576px

All three encode the size in the URL, so a bigger image needs no extra request —
just a rewrite. Each rule is conservative: if the pattern doesn't match, the
original URL is returned untouched, because a broken high-res guess is worse
than a working thumbnail.
"""
import re

# Amazon puts a size directive between the image id and the extension:
# 71RbhzhVbL._AC_UL320_.jpg. Dropping it entirely serves the original upload,
# which is what the product page itself links to.
AMAZON_SIZE_RE = re.compile(r"\._[A-Z0-9_,]+_\.(jpg|jpeg|png)$", re.I)

# IKEA takes a named preset: xxs xs s m l xl xxl.
IKEA_SIZE_RE = re.compile(r"([?&])f=(?:xxs|xs|s|m|l)\b", re.I)

# Walmart sizes via query params, capped around 1000 on their CDN.
WALMART_DIM_RE = re.compile(r"\bodn(Height|Width)=\d+", re.I)
WALMART_TARGET = 1000

# Costco's Adobe asset CDN serves 350px by default: ...?width=350&height=350&fit=contain
COSTCO_DIM_RE = re.compile(r"\b(width|height)=\d+", re.I)
COSTCO_TARGET = 1200


def upscale(url: str | None, site: str) -> str | None:
    """Return the highest-resolution form of `url` we can derive for `site`."""
    if not url or not url.startswith("http"):
        return url

    if site == "amazon" or "media-amazon.com" in url:
        return AMAZON_SIZE_RE.sub(r".\1", url)

    if site == "ikea" or "ikea.com" in url:
        if IKEA_SIZE_RE.search(url):
            return IKEA_SIZE_RE.sub(r"\1f=xl", url)
        return url if "f=" in url else url + ("&" if "?" in url else "?") + "f=xl"

    if site == "walmart" or "walmartimages.com" in url:
        return WALMART_DIM_RE.sub(lambda m: f"odn{m.group(1)}={WALMART_TARGET}", url)

    if site == "costco" or "costco.com" in url:
        return COSTCO_DIM_RE.sub(lambda m: f"{m.group(1)}={COSTCO_TARGET}", url)

    # Temu (img.kwcdn.com) serves whatever width the actor captured — often
    # already 800-1500px — and has no size token to rewrite. Left as-is.
    return url


def best_image(*candidates: str | None, site: str = "") -> str | None:
    """First usable candidate, upscaled.

    Sources disagree about which field holds the real photo — and some hold a
    lazy-load placeholder in the obvious one — so callers pass every field they
    have, in preference order, rather than picking one and hoping.
    """
    for candidate in candidates:
        if candidate and isinstance(candidate, str) and candidate.startswith("http"):
            return upscale(candidate, site)
    return None
