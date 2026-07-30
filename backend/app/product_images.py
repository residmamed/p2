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

# The five rules below were each checked against a live URL from the actor that
# produces it — the rewritten URL returns 200 and a materially bigger payload,
# which is the only evidence that the size token means what it looks like:
#
#     eBay       s-l500.webp        -> s-l1600.webp     20KB ->  86KB
#     Etsy       il_300x300.<id>    -> il_1140xN.<id>   12KB -> 103KB
#     Home Depot ..._600.jpg        -> ..._1000.jpg     17KB ->  37KB
#     Wayfair    resize-h600-w600   -> resize-h1600...  60KB -> 377KB
#     Target     (no token at all)  -> ?wid=1200         9KB ->  90KB

# eBay encodes the longest edge in the filename: s-l64 ... s-l1600.
EBAY_SIZE_RE = re.compile(r"/s-l\d+\.(jpg|jpeg|png|webp)$", re.I)

# Etsy uses named presets in place of a size: il_75x75, il_300x300, il_570xN,
# il_1140xN, il_fullxfull. Only the fixed WxH forms are rewritten — an
# already-large il_1140xN or il_fullxfull is left exactly as it is.
ETSY_SIZE_RE = re.compile(r"/il_\d+x\d+\.", re.I)

# Home Depot suffixes the longest edge before the extension: ..._600.jpg. The
# ladder tops out at 1000; asking for more serves nothing.
THD_SIZE_RE = re.compile(r"_(\d{2,4})\.(jpg|jpeg|png)$", re.I)
THD_TARGET = 1000

# Wayfair states both dimensions in a path segment, URL-encoded ("^compr-r85"
# arrives as %5Ecompr-r85), so the substitution has to stay inside the
# resize-h{n}-w{n} token and leave the rest of the segment untouched.
WAYFAIR_SIZE_RE = re.compile(r"/resize-h\d+-w\d+", re.I)
WAYFAIR_TARGET = 1600

# Target's actor hands back a bare Adobe Scene7 URL with no size directive at
# all, and Scene7's default render is ~9KB. wid= is Scene7's own parameter.
TARGET_SCENE7_TARGET = 1200


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

    if site == "ebay" or "ebayimg.com" in url:
        return EBAY_SIZE_RE.sub(r"/s-l1600.\1", url)

    if site == "etsy" or "etsystatic.com" in url:
        return ETSY_SIZE_RE.sub("/il_1140xN.", url)

    if site == "homedepot" or "thdstatic.com" in url:
        return THD_SIZE_RE.sub(lambda m: f"_{THD_TARGET}.{m.group(2)}", url)

    if site == "wayfair" or "wfcdn.com" in url:
        return WAYFAIR_SIZE_RE.sub(f"/resize-h{WAYFAIR_TARGET}-w{WAYFAIR_TARGET}", url)

    if site == "target" or "target.scene7.com" in url:
        # Only when the actor gave us no directive of its own — if a wid/hei is
        # already present, whoever set it knew more about the asset than we do.
        if "wid=" in url or "hei=" in url:
            return url
        return url + ("&" if "?" in url else "?") + f"wid={TARGET_SCENE7_TARGET}"

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
