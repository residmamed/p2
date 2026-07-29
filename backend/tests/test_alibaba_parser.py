from pathlib import Path

from app.parsing.alibaba_parser import parse_search_results

FIXTURE = Path(__file__).parent / "fixtures" / "alibaba_text_search.html"


def test_parse_search_results_extracts_products():
    html_text = FIXTURE.read_text()
    products = parse_search_results(html_text)

    assert len(products) == 48

    first = products[0]
    assert first.title
    assert first.image_url and first.image_url.startswith("https://")
    assert first.price_min is not None
    assert first.seller_name
    assert first.product_url.startswith("https://www.alibaba.com/product-detail/")
    assert first.contact_type == "form"
    assert first.contact_value and first.contact_value.startswith("https://message.alibaba.com/")


def test_parse_search_results_returns_empty_on_captcha_page():
    products = parse_search_results("<html><title>Captcha Interception</title></html>")
    assert products == []
