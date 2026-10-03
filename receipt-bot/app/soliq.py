"""ofd.soliq.uz chek sahifasi: havolani tahlil qilish, sahifani yuklash va ma'lumotlarni ajratish.

DIQQAT: sahifaning HTML tuzilmasi hujjatlashtirilmagan. Parser jadval sarlavhalaridagi kalit
so'zlar bo'yicha ishlaydi. Sahifa o'zgarsa yoki mos kelmasa, chek `parse_error` holatiga tushadi,
xom HTML bazada saqlanadi va parser tuzatilgach `/api/receipts/{id}/reparse` bilan qayta ishlanadi.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

URL_RE = re.compile(r"https?://ofd\.soliq\.uz/(?:check|epi)\?[^\s<>\"']+", re.IGNORECASE)
MXIK_RE = re.compile(r"(?<!\d)\d{17}(?!\d)")
INN_RE = re.compile(r"(?:STIR|СТИР|ИНН|INN|STIR/JShShIR)\s*[:№]?\s*(\d{9})(?!\d)", re.IGNORECASE)
SELLER_RE = re.compile(r"\b(MChJ|МЧЖ|ООО|OOO|XK|ХК|AJ|АЖ|АО|QK|ЧП|YaTT|ЯТТ|ИП)\b")
NOT_FOUND_RE = re.compile(r"topilmadi|не найден|not found|mavjud emas", re.IGNORECASE)


class CheckUrlError(ValueError):
    pass


class FetchError(Exception):
    """Vaqtinchalik xato: sayt javob bermadi yoki chek hali soliq bazasiga tushmagan."""


@dataclass
class CheckRef:
    url: str
    fm: str            # t - fiskal modul
    check_number: str  # r - chek raqami
    check_date: datetime  # c - sana va vaqt
    fp: str            # s - fiskal belgi


@dataclass
class CheckItem:
    name: str
    qty: float | None = None
    price: float | None = None
    discount: float | None = None
    sum: float | None = None
    vat_rate: float | None = None
    vat_sum: float | None = None
    mxik: str | None = None
    package_code: str | None = None
    unit: str | None = None


@dataclass
class ParsedCheck:
    seller_name: str | None = None
    seller_inn: str | None = None
    address: str | None = None
    total: float | None = None
    vat_total: float | None = None
    items: list[CheckItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def find_check_urls(text: str) -> list[str]:
    seen: list[str] = []
    for url in URL_RE.findall(text or ""):
        url = url.rstrip(".,;)")
        if url not in seen:
            seen.append(url)
    return seen


def parse_check_url(url: str) -> CheckRef:
    parsed = urlparse(url.strip())
    if parsed.hostname is None or parsed.hostname.lower() != "ofd.soliq.uz":
        raise CheckUrlError("Havola ofd.soliq.uz saytiga tegishli emas")
    params = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
    missing = [k for k in ("t", "r", "c", "s") if not params.get(k)]
    if missing:
        raise CheckUrlError(f"Havolada parametrlar yo'q: {', '.join(missing)}")
    try:
        check_date = datetime.strptime(params["c"], "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise CheckUrlError(f"Chek sanasi noto'g'ri: {params['c']}") from exc
    return CheckRef(
        url=url.strip(),
        fm=params["t"],
        check_number=params["r"],
        check_date=check_date,
        fp=params["s"],
    )


async def fetch_check_html(url: str, timeout: float = 30.0) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (receipt-bot)", "Accept-Language": "uz,ru;q=0.8"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"ofd.soliq.uz ga ulanib bo'lmadi: {exc}") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise FetchError(f"ofd.soliq.uz javobi: HTTP {response.status_code}")
    if response.status_code != 200:
        raise FetchError(f"ofd.soliq.uz javobi: HTTP {response.status_code}")
    return response.text


# ---------------------------------------------------------------- HTML tahlili

def parse_number(text: str | None) -> float | None:
    """'123 815,00' / '123815.00' / '1,234.56' / '3.028' / '12%' -> float."""
    if text is None:
        return None
    cleaned = re.sub(r"[\s  ]", "", str(text))
    match = re.search(r"-?\d[\d.,]*", cleaned)
    if not match:
        return None
    value = match.group(0).rstrip(".,")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(",") == 1:
        value = value.replace(",", ".")
    elif value.count(",") > 1:
        value = value.replace(",", "")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    try:
        return float(value)
    except ValueError:
        return None


_COLUMNS = [
    # (maydon, kalit so'zlar) - tartib muhim: aniqrog'i oldin tekshiriladi
    ("mxik", ("mxik", "икпу", "ikpu", "мхик")),
    ("package_code", ("qadoq", "упаков", "package")),
    ("vat_rate", ("qqs stavka", "ставка ндс", "qqs %", "ндс %", "stavka")),
    ("vat_sum", ("qqs", "ндс", "vat")),
    ("discount", ("chegirma", "скидк", "discount")),
    ("qty", ("soni", "miqdor", "кол", "qty", "quantity")),
    ("unit", ("o'lchov", "o‘lchov", "ед.", "единиц", "unit")),
    ("price", ("narx", "цена", "price")),
    ("name", ("nomi", "наимен", "mahsulot", "tovar", "товар", "name", "xizmat")),
    ("sum", ("summa", "сумма", "jami", "итого", "qiymat", "стоимост", "total")),
]


def _map_header(cells: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for idx, cell in enumerate(cells):
        low = cell.lower()
        for name, keys in _COLUMNS:
            if name in used:
                continue
            if any(k in low for k in keys):
                if name == "vat_sum" and "%" in low:
                    name = "vat_rate"
                    if name in used:
                        continue
                mapping[idx] = name
                used.add(name)
                break
    return mapping


def _row_cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]


def _items_from_table(table) -> list[CheckItem]:
    rows = table.find_all("tr")
    header_idx, mapping = None, {}
    for i, row in enumerate(rows[:5]):
        candidate = _map_header(_row_cells(row))
        if "name" in candidate.values() and len(candidate) >= 2:
            header_idx, mapping = i, candidate
            break
    if header_idx is None:
        return []

    items: list[CheckItem] = []
    for row in rows[header_idx + 1:]:
        cells = _row_cells(row)
        if not any(cells):
            continue
        row_text = " ".join(cells)
        values = {mapping[i]: cells[i] for i in mapping if i < len(cells)}
        name = (values.get("name") or "").strip()
        numbers = {
            k: parse_number(values.get(k))
            for k in ("qty", "price", "discount", "sum", "vat_rate", "vat_sum")
        }
        is_item = name and (numbers["sum"] is not None or numbers["price"] is not None)
        mxik_match = MXIK_RE.search(values.get("mxik") or row_text)
        if not is_item:
            # Kod alohida qatorda chiqishi mumkin - oldingi tovarga biriktiramiz.
            if items and mxik_match and not items[-1].mxik:
                items[-1].mxik = mxik_match.group(0)
            continue
        if re.match(r"^(jami|итого|umumiy|всего)", name, re.IGNORECASE):
            continue
        items.append(
            CheckItem(
                name=name,
                mxik=mxik_match.group(0) if mxik_match else None,
                package_code=(values.get("package_code") or None),
                unit=(values.get("unit") or None),
                **numbers,
            )
        )
    return items


def _lines(soup: BeautifulSoup) -> list[str]:
    return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]


def _amount_after(lines: list[str], pattern: str, exclude: str | None = None,
                  next_line: bool = True) -> float | None:
    regex = re.compile(pattern, re.IGNORECASE)
    skip = re.compile(exclude, re.IGNORECASE) if exclude else None
    for i, line in enumerate(lines):
        if regex.search(line) and not (skip and skip.search(line)):
            tail = regex.split(line, maxsplit=1)[-1]
            value = parse_number(tail)
            if value is None and next_line and i + 1 < len(lines):
                value = parse_number(lines[i + 1])
            if value is not None:
                return value
    return None


_NOT_TOTAL = r"qqs|ндс|chegirma|скидк|bonus|бонус"


def parse_check_html(html: str) -> ParsedCheck:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = _lines(soup)

    result = ParsedCheck()
    for table in soup.find_all("table"):
        items = _items_from_table(table)
        if items:
            result.items.extend(items)

    text = "\n".join(lines)
    inn = INN_RE.search(text)
    if inn:
        result.seller_inn = inn.group(1)
    for line in lines:
        if SELLER_RE.search(line) and len(line) < 150:
            result.seller_name = line
            break

    # Avval aniq yorliqlar, keyin umumiy «Jami/Итого» (faqat shu qatorning o'zidagi raqam).
    result.total = _amount_after(
        lines, r"(jami\s+to.?lov|to.?lov\s+uchun|umumiy\s+summa|итого\s+к\s+оплате)\s*:?",
        exclude=_NOT_TOTAL,
    )
    if result.total is None:
        result.total = _amount_after(lines, r"(итого|jami)\s*:?", exclude=_NOT_TOTAL,
                                     next_line=False)
    result.vat_total = _amount_after(lines, r"(jami\s+qqs|shu\s+jumladan\s+qqs|в\s+т\.?\s*ч\.?\s*ндс|ндс\s+итого|qqs\s+jami)\s*:?")

    if result.items:
        if result.total is None:
            result.total = round(sum(i.sum or 0 for i in result.items), 2)
        if result.vat_total is None and any(i.vat_sum is not None for i in result.items):
            result.vat_total = round(sum(i.vat_sum or 0 for i in result.items), 2)
    return result


def looks_not_found(html: str) -> bool:
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    return bool(NOT_FOUND_RE.search(text)) and "<table" not in html.lower()
