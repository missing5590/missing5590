from datetime import datetime

import pytest

from app import soliq

URL = "https://ofd.soliq.uz/check?t=NA000000053505&r=415009&c=20260930102457&s=592431510681"

# Sintetik HTML: ofd.soliq.uz sahifasining haqiqiy tuzilmasi emas, parser mantiqini tekshirish uchun.
HTML = """
<html><body>
<div>«Anglesey Food» MChJ XK</div>
<div>STIR: 202099756</div>
<table>
  <tr><th>Nomi</th><th>Soni</th><th>Narxi</th><th>Chegirma</th><th>QQS stavkasi</th>
      <th>QQS summasi</th><th>Summa</th></tr>
  <tr><td>Tovuq qanoti AF, kg</td><td>3.028</td><td>40 890,00</td><td>0</td><td>12%</td>
      <td>13 265,89</td><td>123 815,00</td></tr>
  <tr><td colspan="7">MXIK: 02064742030280000</td></tr>
  <tr><td>Hol. Bonfile, kg</td><td>1.718</td><td>269 990,00</td><td>0</td><td>12%</td>
      <td>49 697,46</td><td>463 843,00</td></tr>
  <tr><td>Jami</td><td></td><td></td><td></td><td></td><td>62 963,35</td><td>587 658,00</td></tr>
</table>
<div>Jami QQS: 62 963,35</div>
<div>Jami to'lov: 587 658,00</div>
</body></html>
"""


def test_parse_check_url():
    ref = soliq.parse_check_url(URL)
    assert ref.fm == "NA000000053505"
    assert ref.check_number == "415009"
    assert ref.fp == "592431510681"
    assert ref.check_date == datetime(2026, 9, 30, 10, 24, 57)


@pytest.mark.parametrize("url", [
    "https://example.com/check?t=1&r=2&c=20260930102457&s=3",
    "https://ofd.soliq.uz/check?t=1&r=2&s=3",
    "https://ofd.soliq.uz/check?t=1&r=2&c=bad&s=3",
])
def test_parse_check_url_invalid(url):
    with pytest.raises(soliq.CheckUrlError):
        soliq.parse_check_url(url)


def test_find_check_urls():
    text = f"chek: {URL}, yana {URL}"
    assert soliq.find_check_urls(text) == [URL]


@pytest.mark.parametrize("text,expected", [
    ("123 815,00", 123815.0),
    ("123815.00", 123815.0),
    ("1,234.56", 1234.56),
    ("3.028", 3.028),
    ("12%", 12.0),
    ("1.234.567", 1234567.0),
    ("", None),
    ("yo'q", None),
])
def test_parse_number(text, expected):
    assert soliq.parse_number(text) == expected


def test_parse_check_html():
    parsed = soliq.parse_check_html(HTML)
    assert parsed.seller_name == "«Anglesey Food» MChJ XK"
    assert parsed.seller_inn == "202099756"
    assert parsed.total == 587658.0
    assert parsed.vat_total == 62963.35
    assert len(parsed.items) == 2
    first = parsed.items[0]
    assert first.name == "Tovuq qanoti AF, kg"
    assert first.qty == 3.028
    assert first.price == 40890.0
    assert first.sum == 123815.0
    assert first.vat_rate == 12.0
    assert first.vat_sum == 13265.89
    assert first.mxik == "02064742030280000"


def test_parse_html_without_items():
    parsed = soliq.parse_check_html("<html><body>Chek topilmadi</body></html>")
    assert parsed.items == []
    assert soliq.looks_not_found("<html><body>Chek topilmadi</body></html>")
