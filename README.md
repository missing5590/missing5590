# Chekdan 1C ga kirim (Telegram bot)

Korporativ karta bilan qilingan xaridlarni 1C:Бухгалтерия 3.0 ga avtomatik kiritish:

```
Xodim ──chek rasmi / QR havola──▶ Telegram ──▶ receipt-bot (server)
                                               • QR o'qiydi
                                               • ofd.soliq.uz dan chek ma'lumotlarini oladi
                                               • chek raqami bo'yicha dublikatni tekshiradi
                                               • bazaga saqlaydi, xodimga javob beradi
1C [Загрузить] ──HTTP──▶ receipt-bot ──▶ JSON ──▶ «Поступление товаров и услуг» (записан, не проведен)
```

| Papka | Tarkibi |
|---|---|
| `receipt-bot/` | Server: Telegram bot + 1C uchun HTTP API (Python, FastAPI, SQLite) |
| `1c/ЗагрузкаЧековTelegram/` | 1C tashqi qayta ishlash (внешняя обработка) modullari |

---

## 1. Serverga o'rnatish (Linux)

```bash
sudo useradd -r -m -d /opt/receipt-bot receiptbot
sudo -u receiptbot git clone https://github.com/missing5590/missing5590.git /opt/receipt-bot
cd /opt/receipt-bot/receipt-bot
sudo -u receiptbot python3 -m venv .venv
sudo -u receiptbot .venv/bin/pip install -r requirements.txt

sudo -u receiptbot cp .env.example .env
sudo -u receiptbot nano .env        # BOT_TOKEN va API_KEY ni yozing
sudo chmod 600 .env
```

`.env` dagi asosiy sozlamalar:

| O'zgaruvchi | Ma'nosi |
|---|---|
| `BOT_TOKEN` | @BotFather bergan token. **Faqat serverdagi `.env` ga yoziladi, GitHub'ga emas.** |
| `API_KEY` | 1C so'rovlari uchun maxfiy kalit: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `PORT` | 1C ulanadigan port (standart `8090`) |
| `DEDUP_BY` | `check_number` - faqat chek raqami bo'yicha (standart); `check_number_fm` - chek raqami + kassa (FM) |
| `ALLOWED_CHAT_IDS` | Ruxsat berilgan Telegram chat ID'lari. Bo'sh - hammaga ochiq. Begona odam yozsa, bot uning ID'sini aytadi. |

Xizmat sifatida ishga tushirish:

```bash
sudo cp deploy/receipt-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now receipt-bot
sudo journalctl -u receipt-bot -f      # loglar
curl http://localhost:8090/health      # {"ok":true}
```

Yangilash (GitHub'dagi yangi kodni olish): `sudo -u receiptbot /opt/receipt-bot/receipt-bot/deploy/update.sh`

**Talablar:** Python 3.10+, serverdan `api.telegram.org` va `ofd.soliq.uz` ochiq bo'lishi kerak; 1C ishlaydigan
kompyuterlardan esa server porti (`8090`) ochiq bo'lishi kerak. Internet orqali ulansa, oldiga nginx + HTTPS qo'ying.

## 2. Bot qanday ishlaydi

- Xodim botga **chek rasmini** (yaxshisi «fayl» sifatida - Telegram siqmaydi) yoki **QR havolasini** matn qilib yuboradi.
- Bot darhol javob beradi: `✅ Qabul qilindi ... 4 ta qator, jami 588 308,00 so'm` yoki xato sababi.
- Chek raqami allaqachon bo'lsa: `⚠️ Bu chek allaqachon yuborilgan`.
- Soliq sayti javob bermasa yoki chek u yerda hali paydo bo'lmagan bo'lsa, server har `RETRY_INTERVAL` sekundda qayta urinadi.
- 1C hujjat yaratgach, xodimga `📥 1C ga kiritildi: Поступление №...` xabari boradi.

Chek holatlari: `fetch_error` → `ready` → `processing` → `imported` (yoki `parse_error`, `rejected`).

## 3. 1C uchun API

Barcha so'rovlarda `X-API-Key: <API_KEY>` sarlavhasi.

| So'rov | Vazifasi |
|---|---|
| `POST /api/receipts/claim?limit=100` | Tayyor cheklarni olish. Ular `processing` bo'ladi - boshqa kompyuter ularni olmaydi |
| `POST /api/receipts/{id}/ack` `{"document": "..."}` | Hujjat yaratildi |
| `POST /api/receipts/{id}/fail` `{"error": "..."}` | Xato - chek navbatga qaytadi, keyingi yuklashda yana keladi |
| `POST /api/receipts/{id}/reject` `{"error": "..."}` | Rad etish (dublikat kaliti bo'shaydi) |
| `POST /api/receipts/{id}/reparse[?refetch=true]` | Saqlangan sahifani qayta tahlil qilish |
| `GET /api/receipts?status=parse_error` | Ro'yxat (tekshirish uchun) |
| `GET /api/receipts/{id}` / `.../image` | Bitta chek / uning rasmi |

`ack`/`fail` yuborilmagan cheklar `LEASE_MINUTES` daqiqadan keyin avtomatik navbatga qaytadi.

Chek JSON namunasi:

```json
{
  "id": 17, "status": "processing", "check_number": "415009", "fm": "NA000000053505",
  "fp": "592431510681", "check_date": "2026-09-30T10:24:57",
  "url": "https://ofd.soliq.uz/check?t=NA000000053505&r=415009&c=20260930102457&s=592431510681",
  "seller_name": "«Anglesey Food» MChJ XK", "seller_inn": "202099756",
  "total": 588308.0, "vat_total": 63032.99, "last_error": null,
  "items": [{"name": "Tovuq qanoti AF, kg", "qty": 3.028, "price": 40890.0, "discount": 0.0,
             "sum": 123815.0, "vat_rate": 12.0, "vat_sum": 13265.89, "mxik": "...",
             "package_code": null, "unit": null}]
}
```

## 4. 1C qayta ishlashni yig'ish

Konfiguratorda: **Файл → Новый → Внешняя обработка**, nomi `ЗагрузкаЧековTelegram`.

1. Rekvizitlar va «Результаты» tabular qismini `МодульОбъекта.bsl` boshidagi izohdagidek yarating.
2. Asosiy forma yarating: rekvizit maydonlari, `Объект.Результаты` jadvali, `Загрузить` buyrug'i (tugma).
   Jadvalning `Выбор` hodisasini `РезультатыВыбор` ga bog'lang.
3. Obyekt moduliga `МодульОбъекта.bsl`, forma moduliga `МодульФормы.bsl` matnini qo'ying.
4. Saqlang (`.epf`) va 1C:Предприятие'da **Файл → Открыть** orqali oching.

Birinchi ochilishda server manzili (`http://192.168.1.10:8090`), `API_KEY`, organizatsiya va sklad kiritiladi -
ular foydalanuvchi sozlamalarida saqlanadi.

**Hujjat qanday to'ldiriladi:**
- Kontragent - STIR (ИНН) bo'yicha topiladi, bo'lmasa yaratiladi; shartnoma - «С поставщиком», bo'lmasa
  «Покупки по корпоративной карте» yaratiladi.
- Nomenklatura - MXIK rekviziti bo'lsa (КодИКПУ/ИКПУ/КодМХИК/МХИК) shu bo'yicha, keyin nomi bo'yicha.
  Topilmasa: «Создавать номенклатуру» belgilangan bo'lsa yaratiladi, aks holda chek xato bilan navbatga qaytadi.
- `НомерВходящегоДокумента` = chek raqami, `ДатаВходящегоДокумента` = chek sanasi; izohda FM, FP va havola.
- Dublikat: shu `НомерВходящегоДокумента` bilan o'chirilmagan «Поступление» bo'lsa, yangi hujjat yaratilmaydi.
- Hujjat **faqat yoziladi** - buxgalter tekshirib o'zi o'tkazadi.

> Kod 1C:Бухгалтерия 3.0 (RU) metadata nomlari asosida yozilgan. O'zbekiston lokalizatsiyasida rekvizit
> yoki perechislenie nomlari farq qilsa (masalan `СтавкиНДС.НДС12`), birinchi sinovda xato matni aynan
> qaysi nom topilmaganini ko'rsatadi.

## 5. Muhim: ofd.soliq.uz sahifa tuzilmasi

Sahifaning HTML tuzilmasi rasmiy hujjatlashtirilmagan; parser (`receipt-bot/app/soliq.py`) jadval
sarlavhalaridagi kalit so'zlar (Nomi/Soni/Narxi/QQS/MXIK...) bo'yicha ishlaydi va **haqiqiy sahifada hali
sinab ko'rilmagan**. Agar chek `parse_error` holatiga tushsa:

1. Saqlangan HTML bazada qoladi (`receipts.raw_html`) - uni dasturchiga yuboring.
2. Parser tuzatilgach: `POST /api/receipts/{id}/reparse` - chek qayta tahlil qilinadi.

## 6. Testlar

```bash
cd receipt-bot
pip install -r requirements-dev.txt
python -m pytest -q
```
