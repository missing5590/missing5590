# Chekdan 1C ga kirim (Telegram bot)

Korporativ karta bilan qilingan xaridlarni 1C:Бухгалтерия 3.0 ga avtomatik kiritish:

```
Xodim ─▶ Telegram ◀── ICHKI SERVER: receipt-bot (tashqaridan kirib bo'lmaydi)
                          • QR o'qiydi, ofd.soliq.uz dan chek ma'lumotini oladi
                          • chek raqami bo'yicha dublikatni tekshiradi, xodimga javob beradi
                          │ tayyor chekni yuboradi / 1C natijasini o'qiydi (chiquvchi HTTPS)
                          ▼
            https://cheklar.onesoft.uz  ← XOSTING: relay-php (PHP + MySQL)
                          ▲
                          │ claim / ack (chiquvchi HTTPS)
1C [Загрузить] (noutbuk yoki boshqa server) ──▶ «Поступление товаров и услуг» (записан, не проведен)
```

Ichki server ham, 1C ham xostingga **o'zi** ulanadi - hech qayerda kiruvchi port ochish shart emas.

| Papka | Tarkibi |
|---|---|
| `receipt-bot/` | Ichki server: Telegram bot (Python) |
| `relay-php/` | Xosting: cheklar navbati, 1C shu yerdan oladi (PHP + MySQL) |
| `1c/ЗагрузкаЧековTelegram/` | 1C tashqi qayta ishlash (внешняя обработка) modullari |

> Agar 1C ichki serverga to'g'ridan-to'g'ri ulana olsa, xostingsiz ham ishlaydi: `RELAY_URL` bo'sh,
> `API_KEY` to'ldiriladi va 1C da server manzili `http://SERVER:8090` bo'ladi (API yo'llari bir xil).

---

## 0. Xostingni sozlash (DirectAdmin)

1. **Subdomen boshqaruvi** → `cheklar` (hujjat ildizi: Standart) → `/domains/cheklar.onesoft.uz/public_html`.
2. **SSL/TLS Certificates** → `cheklar.onesoft.uz` uchun Let's Encrypt sertifikati.
3. **Ma'lumotlar bazalari** → yangi baza va foydalanuvchi (nomi, foydalanuvchisi, parolini yozib oling).
4. **PHP sozlamalari** → PHP 7.0 yoki yangirog'i (versiyani o'zgartirish shart emas).
5. Fayllarni yuklash (File Manager yoki FTP):
   ```
   /domains/cheklar.onesoft.uz/app.php                 ← relay-php/app.php
   /domains/cheklar.onesoft.uz/config.php              ← relay-php/config.example.php dan nusxa, to'ldiriladi
   /domains/cheklar.onesoft.uz/public_html/index.php   ← relay-php/public_html/index.php
   /domains/cheklar.onesoft.uz/public_html/.htaccess   ← relay-php/public_html/.htaccess
   ```
   `app.php` va `config.php` **public_html ichiga emas**, bir pog'ona yuqoriga qo'yiladi - brauzerdan ochilmaydi.
6. `config.php` da baza ma'lumotlari va ikkita kalit:
   `server_key` (ichki server uchun) va `onec_key` (1C uchun). Kalit yaratish:
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Jadvallar birinchi so'rovda avtomatik yaratiladi.
7. Tekshirish: brauzerda `https://cheklar.onesoft.uz/health` → `{"ok":true}`.

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
| `RELAY_URL` | Xosting manzili: `https://cheklar.onesoft.uz` |
| `RELAY_KEY` | Xostingdagi `config.php` → `server_key` bilan bir xil |
| `API_KEY` | Faqat to'g'ridan-to'g'ri rejimda (1C serverga o'zi ulansa). Xosting rejimida bo'sh qoldiring |
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

**Talablar:** Python 3.10+; serverdan tashqariga `api.telegram.org`, `ofd.soliq.uz` va `cheklar.onesoft.uz`
(443-port) ochiq bo'lishi kerak. Serverga tashqaridan kirish shart emas.

## 2. Bot qanday ishlaydi

- Xodim botga **chek rasmini** (yaxshisi «fayl» sifatida - Telegram siqmaydi) yoki **QR havolasini** matn qilib yuboradi.
- Bot darhol javob beradi: `✅ Qabul qilindi ... 4 ta qator, jami 588 308,00 so'm` yoki xato sababi.
- Chek raqami allaqachon bo'lsa: `⚠️ Bu chek allaqachon yuborilgan`.
- Soliq sayti javob bermasa yoki chek u yerda hali paydo bo'lmagan bo'lsa, server har `RETRY_INTERVAL` sekundda qayta urinadi.
- Tayyor chek har `RELAY_INTERVAL` sekundda xostingga yuboriladi.
- 1C hujjat yaratgach, server buni xostingdan bilib oladi va xodimga `📥 1C ga kiritildi: Поступление №...` yozadi.

Ichki serverdagi holatlar: `fetch_error` → `ready` → `relayed` → `imported` (yoki `parse_error`, `rejected`, `duplicate`).
Xostingdagi holatlar: `ready` → `processing` → `imported` (yoki `rejected`).

## 3. 1C uchun API (xostingda)

Barcha so'rovlarda `X-API-Key: <onec_key>` sarlavhasi. Xuddi shu yo'llar ichki serverda ham bor
(to'g'ridan-to'g'ri rejim uchun, `API_KEY` bilan).

| So'rov | Vazifasi |
|---|---|
| `POST /api/receipts/claim?limit=100` | Tayyor cheklarni olish. Ular `processing` bo'ladi - boshqa kompyuter ularni olmaydi |
| `POST /api/receipts/{id}/ack` `{"document": "..."}` | Hujjat yaratildi |
| `POST /api/receipts/{id}/fail` `{"error": "..."}` | Xato - chek navbatga qaytadi, keyingi yuklashda yana keladi |
| `POST /api/receipts/{id}/reject` `{"error": "..."}` | Rad etish (dublikat kaliti bo'shaydi) |
| `GET /api/receipts?status=ready` | Ro'yxat (tekshirish uchun) |
| `GET /api/receipts/{id}` | Bitta chek |

Ichki server xosting bilan `server_key` orqali gaplashadi: `POST /api/relay/push` (tayyor chek),
`GET /api/relay/events?after=N` (1C natijalari).

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

Birinchi ochilishda server manzili (`https://cheklar.onesoft.uz`), kalit (`onec_key`), organizatsiya va sklad kiritiladi -
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

1. Saqlangan HTML ichki server bazasida qoladi (`data/receipts.db`, `receipts.raw_html`) - uni dasturchiga yuboring.
2. Parser tuzatilgach, ichki serverning o'zida: `curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8090/api/receipts/{id}/reparse`
   (buning uchun `.env` da vaqtincha `API_KEY` qo'yiladi) - chek qayta tahlil qilinadi va xostingga yuboriladi.

## 6. Testlar

```bash
cd receipt-bot
pip install -r requirements-dev.txt
python -m pytest -q        # relay testlari uchun php kerak (bo'lmasa o'tkazib yuboriladi)
```
