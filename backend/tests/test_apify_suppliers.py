"""Apify supplier-actor mapping tests — no network.

The payloads below are trimmed from real runs against one live product photo.
They pin the things that would be silently wrong rather than loudly broken: the
site each actor is pointed at, the currency 1688 quotes in, and the seller
fields that are the whole reason these actors replaced the browser path.
"""
from app.apify_suppliers import ACTORS, _1688_product, _alibaba_product

ALIBABA_ITEM = {
    "success": True,
    "site": "alibaba",
    "productId": "1601617346215",
    "title": "Custom 40oz Tumbler with Handle and Straw Outdoor Travel Mug Stainless Steel",
    "price": 2.42,
    "currency": "USD",
    "minOrderQty": "2",
    "imageUrl": "https://s.alicdn.com/@sc04/kf/Ha6f654.jpg_300x300.jpg",
    "productUrl": "https://www.alibaba.com/product-detail/Custom-40oz-Tumbler_1601617346215.html",
    "supplierName": "Yongkang Baimuyu Industries And Trading Co., Ltd.",
    "supplierUrl": "https://hibmy.en.alibaba.com/",
    "location": "CN",
    "reviewScore": "4.5",
}

ITEM_1688 = {
    "provider": "1688",
    "title": "跨境40oz车载汽车杯双层真空304不锈钢保温杯手柄便携吸管冰霸杯",
    "product_url": "https://detail.1688.com/offer/798565682005.html",
    "image_url": "https://cbu01.alicdn.com/img/ibank/x.jpg",
    "price_min": 15.0,
    "price_max": 15.0,
    "currency": "CNY",
    "currency_code": "CNY",
    "unit": "个",
    "moq": 2,
    "shop_name": "永康市屹力工贸有限公司",
    "shop_url": "https://yiligongmao.1688.com?tracelog=p4p",
    "rating": 3.2334712,
    "review_count": None,
    "sold_count": 466,
    "product_id": "798565682005",
}


def test_each_actor_is_pointed_at_the_site_it_is_supposed_to_search():
    """One actor covers Alibaba, 1688 and AliExpress behind a `destination`, and
    the other behind a `provider`. Lose either and the search silently returns
    a different marketplace's listings."""
    assert ACTORS["alibaba"].build_input("http://x/i.jpg")["destination"] == "alibaba"
    assert ACTORS["1688"].build_input("http://x/i.jpg")["provider"] == "1688"
    # Alibaba's actor takes a single URL, 1688's takes a list — an easy swap.
    assert ACTORS["alibaba"].build_input("http://x/i.jpg")["imageUrl"] == "http://x/i.jpg"
    assert ACTORS["1688"].build_input("http://x/i.jpg")["imageUrls"] == ["http://x/i.jpg"]


def test_alibaba_row_names_the_supplier_and_links_its_company_page():
    """The reason this actor replaced the browser path: Supplier Resolution
    measured 0 of 49 listings with any seller field, and each one it did fix
    cost a product-page fetch. Every row here arrives with both."""
    p = _alibaba_product(ALIBABA_ITEM)
    assert p.seller_name == "Yongkang Baimuyu Industries And Trading Co., Ltd."
    assert p.seller_url == "https://hibmy.en.alibaba.com/"
    assert p.price_min == 2.42 and p.price_text == "$2.42"
    assert p.currency == "USD"
    assert p.moq == "MOQ 2"
    assert p.rating == 4.5
    assert p.site == "alibaba"


def test_1688_prices_stay_in_yuan():
    """1688 quotes in CNY. Relabelling ¥15 as $15 would overstate a supplier
    quote sevenfold, and there is no rate in this codebase to convert with."""
    p = _1688_product(ITEM_1688)
    assert p.currency == "CNY"
    assert p.price_text == "¥15.00"
    assert p.price_min == 15.0


def test_1688_reversed_price_range_is_ordered():
    """Measured on a live row: `price_min: 9.4, price_max: 8.5`. Passed through
    as-is the card would read "¥9.40 - ¥8.50"."""
    p = _1688_product({**ITEM_1688, "price_min": 9.4, "price_max": 8.5})
    assert p.price_min == 8.5 and p.price_max == 9.4
    assert p.price_text == "¥8.50 - ¥9.40"


def test_1688_row_carries_shop_moq_and_demand():
    p = _1688_product(ITEM_1688)
    assert p.seller_name == "永康市屹力工贸有限公司"
    assert p.seller_url == "https://yiligongmao.1688.com?tracelog=p4p"
    assert p.moq == "MOQ 2个"
    # Full float precision is not accuracy — the UI renders one decimal.
    assert p.rating == 3.23
    assert p.popularity_score == 466


def test_rows_without_a_title_or_url_are_dropped():
    assert _alibaba_product({**ALIBABA_ITEM, "productUrl": ""}) is None
    assert _alibaba_product({**ALIBABA_ITEM, "title": "  "}) is None
    assert _1688_product({**ITEM_1688, "product_url": ""}) is None
    assert _1688_product({**ITEM_1688, "title": "", "original_title": ""}) is None


def test_a_priceless_row_is_kept_without_inventing_a_price():
    p = _alibaba_product({**ALIBABA_ITEM, "price": None})
    assert p is not None
    assert p.price_min is None and p.price_text is None and p.currency is None


def test_the_listings_own_seller_name_beats_the_scraped_page_title():
    """Alibaba's company page parses to its <title> — measured live: five rows
    became "Company Overview - Yongkang Baimuyu Industri...". The frontend
    renders the profile's name in preference to the listing's, so without this
    the actor's clean company name is displaced on every enriched row."""
    from app.models import SourcingResult, SupplierProfile
    from app.sourcing import _prefer_listing_seller_name

    product = _alibaba_product(ALIBABA_ITEM)
    result = SourcingResult(
        product=product,
        supplier=SupplierProfile(
            site="alibaba",
            supplier_url="https://hibmy.en.alibaba.com/",
            company_name="Company Overview - Yongkang Baimuyu Industri...",
            years_active=6,
        ),
    )
    _prefer_listing_seller_name([result])
    assert result.supplier.company_name == "Yongkang Baimuyu Industries And Trading Co., Ltd."
    # Everything the page is genuinely the only source for stays put.
    assert result.supplier.years_active == 6


def test_a_listing_with_no_seller_leaves_the_scraped_name_alone():
    from app.models import SourcingResult, SupplierProfile
    from app.sourcing import _prefer_listing_seller_name

    product = _alibaba_product({**ALIBABA_ITEM, "supplierName": ""})
    result = SourcingResult(
        product=product,
        supplier=SupplierProfile(
            site="alibaba", supplier_url="https://x.en.alibaba.com/", company_name="Shenzhen Foo Ltd"
        ),
    )
    _prefer_listing_seller_name([result])
    assert result.supplier.company_name == "Shenzhen Foo Ltd"
