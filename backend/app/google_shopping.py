"""Google Shopping — a keyword answered by picture instead of by search box.

Every other source in bestsellers.py hands the keyword to a store's own search.
This one goes the long way round, because the question it answers is different:
not "who stocks this?" but "what is being sold that looks like what people post
about this?".

    keyword -> Pinterest images (app/pinterest.py, Apify actor)
            -> Google Lens on each image (app/serp_lens.py, SerpApi)
            -> the pages Lens found selling that thing
            -> keep only the ones whose title matches the keyword

Pinterest is the entry point rather than a stock-photo search because its images
are what people actually save about a product category, and Lens needs a
publicly reachable URL — which a Pinterest CDN image already is, so nothing has
to be uploaded or republished (see serp_lens._publish for why that matters).

**The last step is not optional, and is the whole difficulty.** A Pinterest image
for "desk lamp" is a photograph of a room, and Lens answers with what it sees in
that photograph: the desk, the chair, the rug, the plant. Every one is a real
product on a real shop page, and every one is the wrong answer. So this module
never returns what Lens returned — it returns the subset whose titles actually
describe the thing that was searched for, and the gate is applied here rather
than left to the Claude screen in bestsellers.py, which is a kill-switchable
refinement and can be off.

**Cost.** One Apify actor run for Pinterest, then two SerpApi credits per image
(exact + visual are separate calls). At PINTEREST_IMAGES=4 that is 8 credits per
search against a quota shared with Amazon, Walmart and the photo search — which
is why the image count is deliberately small and why this site is opt-in rather
than part of the default set.
"""
import asyncio
import re

from . import pinterest, serp_lens
from .models import Product

SITE = "google_shopping"
LABEL = "Google Shopping"

# How many Pinterest images to run through Lens. Each one costs 2 SerpApi
# credits, and the returns fall off fast: the same handful of shop pages come
# back across images of the same category, so a fifth image mostly re-buys rows
# that dedupe away.
PINTEREST_IMAGES = 4
# Asked for more than are used because Pinterest returns pins with no usable
# image, and a pin whose image Lens can't fetch is a wasted slot.
PINTEREST_FETCH = 12
# Ceiling on what one search contributes to the merged grid, so a source with no
# ranking signal can't crowd out stores that have one.
MAX_RESULTS = 40

# Words that carry no product meaning, so they can't be counted as a match. A
# query is usually two or three words and a title is a marketing sentence, so
# without this "the" alone would qualify half the web.
STOPWORDS = frozenset("""
a an the and or of for with without in on at to from by this that these those
your our my his her its new best top cheap sale buy shop set pack piece pieces
pcs style styles design designs modern vintage
""".split())

# Titles arrive as "Modern Desk Lamp, 3-Colour · Amazon.com" — punctuation and
# separators are noise, so both sides are reduced to bare word tokens.
TOKEN_RE = re.compile(r"[a-z0-9]+")

# What fraction of the keyword's meaningful words a title must contain:
# "stainless steel water bottle" needs two of its four.
MIN_TOKEN_OVERLAP = 0.5
# ...but never fewer than two words when the keyword has two. Measured, on a live
# "desk lamp" run: at a bare 50% a two-word keyword is satisfied by its head noun
# alone, so "desk" was never actually required and the grid filled with lamp
# shades, ceiling lights and floor lamps — every one of them containing "lamp"
# and none of them a desk lamp. Requiring both words is what makes a short
# keyword mean what it says.
MIN_WORDS_REQUIRED = 2

# Hosts that are never the answer, however well their titles match.
#
# Pinterest is here because the chain starts there: a Pinterest board that
# Google Lens matched back to the Pinterest image we searched with is a circle,
# not a shop. The rest are link aggregators and marketplace-of-marketplaces
# pages that Lens surfaces for furniture and lighting queries in particular.
BLOCKED_HOSTS = (
    "pinterest.",
    "pinimg.com",
    "lookaside.",
)


def _words(text: str) -> list[str]:
    """Meaningful lowercase words, in order, deduplicated.

    Order is preserved because the last one carries the most weight — see
    `describes` — and duplicates are dropped so a title that repeats "lamp"
    four times for SEO doesn't count four times.
    """
    out: list[str] = []
    for word in TOKEN_RE.findall((text or "").lower()):
        if word in STOPWORDS or len(word) < 2 or word in out:
            continue
        out.append(word)
    return out


def _variants(word: str) -> set[str]:
    """The word and its other number ("lamp"/"lamps").

    Crude on purpose. A real stemmer would be a dependency and a new failure
    mode for a gate whose whole job is deciding whether two short strings talk
    about the same thing, and singular/plural is the only inflection that
    actually shows up between a search box and a product title.

    Both directions have to be generated, not just one. Returning only
    {word, word[:-1]} for anything ending in "s" meant a singular query never
    reached its own "-es" plural: "glass" produced {"glass", "glas"}, never
    "glasses", so the head-noun gate below dropped every listing for the thing
    the user actually searched. The same silently emptied dishes, boxes and
    brushes -- whole categories answering with nothing while the warning blamed
    the source imagery.
    """
    forms = {word}
    if word.endswith("es") and len(word) > 4:
        # Already plural. "-es" is ambiguous -- "dishes" drops two letters,
        # "glasses" drops one -- so both stems are offered and the caller's set
        # intersection picks whichever is a real word in the other string.
        forms |= {word[:-2], word[:-1]}
    elif word.endswith("s") and len(word) > 3:
        # Ambiguous the other way: "lamps" is a plural, "glass" is a singular
        # that pluralises to "glasses". Offer both readings.
        forms |= {word[:-1], word + "es"}
    else:
        # Sibilant endings take "-es" ("box" -> "boxes", "brush" -> "brushes");
        # everything else takes "-s". Getting this wrong produced "boxs", which
        # matches nothing.
        forms.add(word + ("es" if word.endswith(("x", "ch", "sh")) else "s"))
    return forms


def describes(query: str, title: str) -> bool:
    """Does `title` describe the thing `query` asked for?

    Two conditions, because either alone lets the wrong thing through:

      1. The head noun must be present. In an English product phrase the last
         word is the thing itself and the ones before it are qualifiers —
         "stainless steel water BOTTLE", "cordless DRILL". Overlap alone can't
         see this, so "Stainless Steel Cutlery Set" scored two of four against
         "stainless steel water bottle" and passed a 50% threshold while being
         a completely different product.
      2. Enough of the qualifiers must also match, so that "Lamp Shade
         Replacement" doesn't ride in on the head noun of "desk lamp".

    Generous about wording, strict about subject: "Table Lamp" answers "desk
    lamp" because the subject agrees, while "Office Chair" — photographed at the
    same desk, and genuinely what Lens saw — does not.

    When the query has no meaningful words at all (someone searched "the best"),
    everything passes: a gate with nothing to compare against must not silently
    empty the grid.
    """
    wanted = _words(query)
    if not wanted:
        return True

    have = set()
    for word in _words(title):
        have |= _variants(word)
    if not have:
        return False

    # 1. The head noun.
    if not (_variants(wanted[-1]) & have):
        return False
    # 2. Enough of the phrase overall (the head noun counts towards this).
    hits = sum(1 for word in wanted if _variants(word) & have)
    needed = round(len(wanted) * MIN_TOKEN_OVERLAP)
    needed = min(len(wanted), max(MIN_WORDS_REQUIRED, needed)) if len(wanted) > 1 else 1
    return hits >= needed


def _is_blocked(url: str) -> bool:
    host = (url or "").lower()
    return any(blocked in host for blocked in BLOCKED_HOSTS)


def _shopping_order(product: Product) -> tuple:
    """Priced listings first.

    Not a ranking claim — rank_basis stays `relevance` and the merge weights
    these rows lowest either way. It's that Lens returns a mix of shop pages and
    editorial ones, and on a source called Shopping the ones you can actually buy
    from should not be below a blog post about the same lamp. Measured on the
    live "desk lamp" run: a minority of Lens rows carry a price at all.
    """
    return (0 if product.price_text else 1,)


async def _lens_for_image(image) -> tuple[list[Product], list[str]]:
    """Lens one Pinterest image. Never raises — one dead image must not sink the
    other three. Deduplication is the caller's job, since the same shop page can
    come back from several images and only the caller sees all of them."""
    try:
        products, warnings = await serp_lens.search_by_url(image.image_url)
    except serp_lens.SerpLensError as e:
        return [], [f"[{LABEL}] Lens failed for one inspiration image: {e}"]
    except Exception as e:  # noqa: BLE001 - a single image is never fatal
        return [], [f"[{LABEL}] Lens failed for one inspiration image ({type(e).__name__})."]

    for product in products:
        # Re-tagged from google_lens/google_lens_exact so the row belongs to the
        # source pill the user actually selected, and so the badge says where it
        # came from rather than naming an internal transport.
        product.site = SITE
        # The Pinterest image that led here. Kept because it's the only record of
        # why this row is in the grid at all — the keyword never touched a shop.
        product.inspiration_image_url = image.image_url
    return products, warnings


async def search(query: str) -> tuple[list[Product], list[str]]:
    """Run the whole chain for one keyword. Returns (products, warnings) and
    never raises, matching every other transport in bestsellers.py."""
    if not serp_lens.is_configured():
        return [], [f"[{LABEL}] Needs SERPAPI_KEY — Google Lens is the second half of this source."]

    try:
        images = await pinterest.search_pinterest(query, n=PINTEREST_FETCH)
    except pinterest.PinterestError as e:
        return [], [f"[{LABEL}] Pinterest step failed: {e}"]
    except Exception as e:  # noqa: BLE001 - one site must not sink the merge
        return [], [f"[{LABEL}] Pinterest step failed ({type(e).__name__})."]

    usable = [i for i in images if i.image_url][:PINTEREST_IMAGES]
    if not usable:
        return [], [f"[{LABEL}] Pinterest returned no usable images for this keyword."]

    results = await asyncio.gather(*(_lens_for_image(i) for i in usable))

    seen: set[str] = set()
    found: list[Product] = []
    warnings: list[str] = []
    for products, warned in results:
        warnings.extend(warned)
        for product in products:
            if product.product_url in seen:
                continue
            seen.add(product.product_url)
            found.append(product)

    if not found:
        return [], warnings or [f"[{LABEL}] Google Lens found nothing for these images."]

    # The gate. Everything above this line is "what does the web sell that looks
    # like these pictures"; only this line makes it an answer to the keyword.
    matched = [
        p for p in found
        if describes(query, p.title) and not _is_blocked(p.product_url)
    ]
    if not matched:
        return [], warnings + [
            f"[{LABEL}] Lens found {len(found)} listings from these Pinterest images, "
            f"but none of their titles describe \"{query}\" — the images were most "
            f"likely scenes rather than product shots."
        ]

    dropped = len(found) - len(matched)
    if dropped:
        # Said out loud because this number is the difference between this source
        # working and it quietly listing the furniture behind the product.
        warnings.append(
            f"[{LABEL}] Dropped {dropped} of {len(found)} Lens results whose titles "
            f"don't describe \"{query}\" — other things pictured alongside it."
        )

    # `sorted` is stable, so within priced and unpriced the Lens ordering — best
    # visual match first — is preserved.
    return sorted(matched, key=_shopping_order)[:MAX_RESULTS], warnings
