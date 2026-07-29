from pathlib import Path

from app.parsing.aliexpress_parser import parse_search_results

FIXTURE = Path(__file__).parent / "fixtures" / "aliexpress_search.html"


def test_parse_search_results_extracts_products():
    html_text = FIXTURE.read_text()
    products = parse_search_results(html_text)

    assert len(products) == 60

    first = products[0]
    assert first.title
    assert first.image_url and first.image_url.startswith("https://")
    assert first.product_url.startswith("https://www.aliexpress.com/item/")
    assert first.contact_type == "form"


def test_parse_search_results_returns_empty_on_missing_data():
    products = parse_search_results("<html><body>no data here</body></html>")
    assert products == []
