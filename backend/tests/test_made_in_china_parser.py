from pathlib import Path

from app.parsing.made_in_china_parser import parse_search_results

FIXTURE = Path(__file__).parent / "fixtures" / "made_in_china_search.html"


def test_parse_search_results_extracts_products():
    html_text = FIXTURE.read_text()
    products = parse_search_results(html_text)

    assert len(products) == 30

    first = products[0]
    assert first.title
    assert first.image_url and first.image_url.startswith("https://")
    assert first.price_min is not None
    assert first.seller_name
    assert first.product_url.startswith("https://")
    assert first.contact_type == "form"
    assert first.contact_value and "sendInquiry" in first.contact_value


def test_parse_search_results_returns_empty_on_missing_nodes():
    products = parse_search_results("<html><body>no results</body></html>")
    assert products == []
