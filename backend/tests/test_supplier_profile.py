"""Unit tests for supplier-page parsing — no live calls, same as the product
parser tests. These pin the two behaviours that matter most: contact details are
only reported when genuinely published, and marketplace boilerplate never gets
mistaken for a supplier's own address."""
from app.supplier_profile import parse_supplier_page

ALIBABA_LIKE = """
<html><head>
  <meta property="og:site_name" content="Shenzhen Kaiyue Technology Co., Ltd."/>
  <title>Shenzhen Kaiyue Technology Co., Ltd. - Tumblers, Bottles</title>
</head><body>
  <div class="company-info">
    <span class="location">Guangdong, China</span>
    <span>9 yrs</span>
    <span>Verified Supplier</span>
    <span>Manufacturer</span>
  </div>
  <a href="mailto:service@alibaba.com">Help</a>
  <a href="mailto:sales@kaiyue-cn.com">Contact</a>
  <a href="https://wa.me/8613800138000">WhatsApp</a>
  <a href="tel:+86-755-1234-5678">Call</a>
</body></html>
"""

FORM_ONLY = """
<html><head><title>Ningbo Homeware Imp & Exp Co., Ltd.</title></head>
<body>
  <h1>Ningbo Homeware Imp &amp; Exp Co., Ltd.</h1>
  <div>Trading Company</div>
  <div>所在地： 浙江 宁波</div>
  <div>第 5 年</div>
  <a href="/contact-supplier.html">Send Inquiry</a>
  <a href="mailto:noreply@made-in-china.com">Unsubscribe</a>
</body></html>
"""


def test_extracts_company_facts_and_direct_contacts():
    p = parse_supplier_page(ALIBABA_LIKE, "https://kaiyue.en.alibaba.com", "alibaba")

    assert p.company_name == "Shenzhen Kaiyue Technology Co., Ltd."
    assert p.location == "Guangdong, China"
    assert p.years_active == 9
    assert p.business_type == "manufacturer"
    assert p.verified is True
    assert p.whatsapp == ["8613800138000"]
    assert p.phones == ["+86-755-1234-5678"]


def test_marketplace_boilerplate_emails_are_not_treated_as_supplier_contacts():
    p = parse_supplier_page(ALIBABA_LIKE, "https://kaiyue.en.alibaba.com", "alibaba")

    assert "sales@kaiyue-cn.com" in p.emails
    assert not any("alibaba.com" in e for e in p.emails)


def test_form_only_supplier_reports_no_direct_contact_rather_than_guessing():
    """The README's finding: contact_type is "form" almost everywhere. A page
    with only an inquiry link must yield empty contact lists, not a fabricated
    or boilerplate address."""
    p = parse_supplier_page(FORM_ONLY, "https://nbhome.en.made-in-china.com", "made_in_china")

    assert p.company_name == "Ningbo Homeware Imp & Exp Co., Ltd."
    assert p.emails == []
    assert p.whatsapp == []
    assert p.business_type == "trading company"
    assert p.verified is None  # absent, not False-as-a-guess


def test_parses_chinese_years_and_location():
    p = parse_supplier_page(FORM_ONLY, "https://nbhome.en.made-in-china.com", "made_in_china")

    assert p.years_active == 5
    assert p.location is not None and "宁波" in p.location
