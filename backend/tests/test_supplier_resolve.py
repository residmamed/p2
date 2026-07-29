"""Stage 2.5 (product page -> supplier) and the two data-correctness fixes that
came out of running it against live Alibaba pages.

The interesting cases here are all about *not* reporting something. A supplier
name taken from the wrong field, a company page that is really a bot check, and
a copyright range parsed as a phone number are each worse than an empty column,
because they look like answers.
"""

import asyncio

import pytest

from app import supplier_profile, supplier_resolve
from app.models import Product

ALIBABA_PRODUCT = "https://www.alibaba.com/product-detail/Tumbler_1601780609717.html"


def product(site: str = "alibaba", **kw) -> Product:
    return Product(site=site, title="20oz Tumbler", product_url=ALIBABA_PRODUCT, **kw)


# ---------------------------------------------------------------------------
# Company URL patterns — both live Alibaba minisite shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html,expected",
    [
        (
            '<a href="https://wanshenghome.en.alibaba.com/company_profile">Company</a>',
            "https://wanshenghome.en.alibaba.com/company_profile",
        ),
        (
            '<a href="https://us1574989999xmzl.trustpass.alibaba.com/company_profile.html">C</a>',
            "https://us1574989999xmzl.trustpass.alibaba.com/company_profile.html",
        ),
    ],
)
def test_both_alibaba_minisite_shapes_are_found(html, expected):
    """Probing only .en. left 1 of 4 live listings named-but-unlinked."""
    assert supplier_resolve._company_url(html, "alibaba") == expected


def test_mobile_minisite_is_not_mistaken_for_the_desktop_page():
    html = '<a href="https://us157.m.trustpass.alibaba.com/company_profile.html">m</a>'
    assert supplier_resolve._company_url(html, "alibaba") is None


def test_unknown_site_has_no_pattern():
    assert supplier_resolve._company_url("<a href='http://x.com'>x</a>", "ebay") is None


# ---------------------------------------------------------------------------
# Field extraction — only what the page actually supports
# ---------------------------------------------------------------------------


def test_brand_is_the_supplier_on_b2b_sites():
    data = {"brand": {"name": "Yiwu Wansheng Glass Products Co., Ltd."}}
    assert supplier_resolve._brand_name(data, "alibaba") == "Yiwu Wansheng Glass Products Co., Ltd."
    assert supplier_resolve._brand_name(data, "1688") == "Yiwu Wansheng Glass Products Co., Ltd."


def test_brand_is_not_taken_as_the_seller_on_aliexpress():
    """AliExpress is a consumer marketplace: brand is the product's brand and is
    frequently not the store, so taking it would be a plausible wrong answer."""
    data = {"brand": {"name": "Stanley"}}
    assert supplier_resolve._brand_name(data, "aliexpress") is None


def test_moq_is_read_from_structured_properties():
    data = {"additionalProperties": [{"name": "moq", "value": "25pcs"}]}
    assert supplier_resolve._moq(data) == "25pcs"


def test_missing_moq_stays_none():
    assert supplier_resolve._moq({"additionalProperties": [{"name": "color", "value": "red"}]}) is None


def test_product_page_profile_carries_no_badges_or_contacts():
    """Verification badges and years appear all over Alibaba's page chrome, so
    they must not be attributed to this seller."""
    p = product(seller_url="https://x.en.alibaba.com/company_profile")
    data = {
        "brand": {"name": "Yiwu Wansheng Glass Products Co., Ltd."},
        "additionalProperties": [{"name": "place of origin", "value": "Zhejiang, China"}],
    }
    profile = supplier_resolve._profile_from_product_page(p, data)

    assert profile.company_name == "Yiwu Wansheng Glass Products Co., Ltd."
    assert profile.location == "Zhejiang, China"
    assert profile.verified is None
    assert profile.years_active is None
    assert profile.emails == [] and profile.phones == []
    assert "bot check" in profile.warning


# ---------------------------------------------------------------------------
# resolve() — scope and degradation
# ---------------------------------------------------------------------------


class FakeZyte:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    async def extract_with_product(self, url, timeout=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


def test_resolve_fills_seller_and_moq():
    zyte = FakeZyte(
        {
            "product": {
                "brand": {"name": "Yiwu Wansheng Glass Products Co., Ltd."},
                "additionalProperties": [{"name": "moq", "value": "25pcs"}],
            },
            "browserHtml": '<a href="https://wanshenghome.en.alibaba.com/company_profile">c</a>',
        }
    )
    p = product()

    warnings, profiles = asyncio.run(supplier_resolve.resolve([p], zyte))

    assert p.seller_name == "Yiwu Wansheng Glass Products Co., Ltd."
    assert p.seller_url == "https://wanshenghome.en.alibaba.com/company_profile"
    assert p.moq == "25pcs"
    assert p.contact_type == "form"
    assert profiles[p.product_url].company_name == p.seller_name
    assert any("Identified the supplier behind 1" in w for w in warnings)


def test_resolve_skips_listings_that_already_name_a_seller():
    """Made-in-China's own parser supplies sellers; those must cost nothing."""
    zyte = FakeZyte()
    p = product(site="made_in_china", seller_name="Ningbo Co.", seller_url="https://x.com")

    warnings, profiles = asyncio.run(supplier_resolve.resolve([p], zyte))

    assert zyte.calls == 0
    assert warnings == [] and profiles == {}


def test_resolve_is_capped():
    zyte = FakeZyte({"product": {}, "browserHtml": ""})
    products = [product() for _ in range(30)]

    asyncio.run(supplier_resolve.resolve(products, zyte))

    assert zyte.calls == supplier_resolve.RESOLVE_TOP_N


def test_resolve_reports_a_dead_page_without_sinking_the_batch():
    from app.zyte_client import ZyteError

    zyte = FakeZyte(error=ZyteError("520 website ban"))
    p = product()

    warnings, profiles = asyncio.run(supplier_resolve.resolve([p], zyte))

    assert p.seller_name is None  # nothing invented
    assert profiles == {}
    assert any("could not be opened" in w for w in warnings)


# ---------------------------------------------------------------------------
# supplier_profile — the two wrong-data fixes
# ---------------------------------------------------------------------------


CHALLENGE_HTML = """
<html><head><title>Captcha Interception</title></head>
<body><p>Please slide to verify</p><footer>&copy; 1999-2026 Alibaba.com</footer>
<a href="/verified_supplier">Verified Supplier</a></body></html>
"""


def test_challenge_page_yields_no_company_and_says_why():
    """Live run: all four supplier pages were bot checks, and the parser
    reported "Captcha Interception" as the company name."""
    assert supplier_profile.is_challenged(CHALLENGE_HTML)

    profile = supplier_profile.parse_supplier_page(CHALLENGE_HTML, "https://x/company", "alibaba")

    assert profile.company_name is None
    assert profile.phones == [] and profile.emails == []
    assert profile.verified is None
    assert "bot check" in profile.warning


def test_copyright_range_is_not_a_phone_number():
    html = "<html><body>Established supplier. &copy; 1999-2026 All rights reserved.</body></html>"
    profile = supplier_profile.parse_supplier_page(html, "https://x/company", "alibaba")
    assert profile.phones == []


def test_a_real_page_still_parses():
    html = """
    <html><head><title>Ningbo Best Co., Ltd.</title></head>
    <body>Manufacturer. 12 yrs. Verified Supplier.
    <a href="mailto:sales@ningbobest.com">mail</a></body></html>
    """
    profile = supplier_profile.parse_supplier_page(html, "https://x/company", "alibaba")

    assert profile.company_name == "Ningbo Best Co., Ltd."
    assert profile.emails == ["sales@ningbobest.com"]
    assert profile.years_active == 12
    assert profile.business_type == "manufacturer"
    assert profile.verified is True
    assert profile.warning is None


# ---------------------------------------------------------------------------
# Multi-page company scan
# ---------------------------------------------------------------------------


def test_company_scan_walks_several_pages_of_the_company_site():
    """Contact details live on a contact page far more often than on whatever
    page a product listing happened to link to."""
    urls = supplier_profile.company_page_urls(
        "https://wanshenghome.en.alibaba.com/company_profile.html", "alibaba"
    )
    assert urls[0] == "https://wanshenghome.en.alibaba.com/company_profile.html"
    assert "https://wanshenghome.en.alibaba.com/contactinfo.html" in urls
    assert len(urls) <= supplier_profile.MAX_PAGES_PER_COMPANY


def test_company_scan_is_bounded():
    urls = supplier_profile.company_page_urls("https://x.en.alibaba.com/a.html", "alibaba")
    assert len(urls) == supplier_profile.MAX_PAGES_PER_COMPANY


def test_pages_are_merged_not_overwritten():
    """A supplier publishing an email on About and a phone on Contact has both."""
    from app.models import SupplierProfile

    a = SupplierProfile(site="alibaba", supplier_url="u", emails=["a@x.com"], company_name="X Ltd")
    b = SupplierProfile(site="alibaba", supplier_url="u", phones=["+86 1"], location="Ningbo")

    merged = supplier_profile.merge_profiles(a, b)

    assert merged.emails == ["a@x.com"]
    assert merged.phones == ["+86 1"]
    assert merged.company_name == "X Ltd"
    assert merged.location == "Ningbo"


def test_scanned_but_nothing_published_says_so():
    """'They publish none' and 'we could not look' are different facts."""
    html = "<html><head><title>Ningbo Co</title></head><body>We make cups.</body></html>"
    profile = supplier_profile.parse_supplier_page(html, "https://x/company", "alibaba")
    assert profile.emails == [] and profile.phones == []
    assert profile.company_name == "Ningbo Co"


# ---------------------------------------------------------------------------
# Embedded supplier JSON on the product page
# ---------------------------------------------------------------------------

EMBEDDED = (
    '{\\"companyBusinessType\\":\\"Manufacturer,Trading Company\\",'
    '\\"companyJoinYears\\":\\"4\\",\\"companyId\\":280221959,'
    '\\"companyName\\":\\"Yiwu Wansheng Glass Products Co., Ltd.\\",'
    '\\"companyProfileUrl\\":\\"https:\\/\\/wanshenghome.en.alibaba.com\\/company_profile.html\\",'
    '\\"companyRegisterCountry\\":\\"CN\\",\\"contactName\\":\\"Ms. zhao\\",'
    '\\"baoDisplayAssurance\\":true}'
)


def test_embedded_supplier_record_is_mined_from_the_product_page():
    profile = supplier_resolve._profile_from_embedded_json(product(), EMBEDDED)

    assert profile.company_name == "Yiwu Wansheng Glass Products Co., Ltd."
    assert profile.contact_name == "Ms. zhao"
    assert profile.business_type == "manufacturer,trading company"
    assert profile.years_active == 4
    assert profile.location == "CN"
    assert profile.verified is True
    assert profile.supplier_url == "https://wanshenghome.en.alibaba.com/company_profile.html"


def test_embedded_record_absent_returns_none():
    assert supplier_resolve._profile_from_embedded_json(product(), "<html>nothing</html>") is None


# ---------------------------------------------------------------------------
# Phone detection — the live false positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "@media (max-width:1920-1200.) {}",  # a screen resolution
        "reference 429-256778",              # an internal id
        "&copy; 1999-2026",                  # a copyright range
    ],
)
def test_unlabelled_digit_runs_are_not_phone_numbers(body):
    profile = supplier_profile.parse_supplier_page(
        f"<html><body>{body}</body></html>", "https://x/c", "alibaba"
    )
    assert profile.phones == []


def test_labelled_phone_is_accepted():
    html = "<html><body>Tel: +86 574 8888 6666</body></html>"
    profile = supplier_profile.parse_supplier_page(html, "https://x/c", "alibaba")
    assert profile.phones == ["+86 574 8888 6666"]


@pytest.mark.parametrize("raw", ["name", "contactName", "N/A", "-", "", " "])
def test_placeholder_contact_names_are_rejected(raw):
    """A live listing returned the literal template value "name" as its contact
    person — the page ships the enquiry form's i18n keys alongside the record."""
    assert supplier_resolve._clean_contact_name(raw) is None


def test_real_contact_name_survives():
    assert supplier_resolve._clean_contact_name("  Ms. zhao ") == "Ms. zhao"


def test_generic_minisite_title_is_not_a_company_name():
    """Live scans returned "Alibaba.com" as the supplier for every listing."""
    for title in ["Alibaba.com", "1688.com", "Company Profile", "Contact Us"]:
        html = f"<html><head><title>{title}</title></head><body>x</body></html>"
        assert supplier_profile.parse_supplier_page(html, "https://x/c", "alibaba").company_name is None


def test_real_minisite_title_is_kept():
    html = "<html><head><title>Yiwu Wansheng Glass Products Co., Ltd.</title></head><body>x</body></html>"
    got = supplier_profile.parse_supplier_page(html, "https://x/c", "alibaba").company_name
    assert got == "Yiwu Wansheng Glass Products Co., Ltd."
