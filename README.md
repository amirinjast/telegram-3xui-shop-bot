# Telegram 3x-ui Shop Bot

ربات فروشگاهی 3x-ui با پنل مدیریتی دکمه‌ای داخل تلگرام، SQLite، پرداخت NOWPayments، پرداخت کارت‌به‌کارت، کیف پول، خرید مستقیم، تمدید سرویس، QR Code و مدیریت اینباندها.

این نسخه برای کار با گوشی راحت‌تر شده: فقط `BOT_TOKEN` را داخل `.env` می‌گذاری، ربات بالا می‌آید، اولین `/start` ادمین اصلی می‌شود و بقیه تنظیمات از داخل ربات و با دکمه انجام می‌شود.

## امکانات اصلی

- نصب فقط با توکن تلگرام
- ذخیره تنظیمات، کاربران، پرداخت‌ها و سرویس‌ها داخل SQLite
- منوی اصلی و پنل ادمین با دکمه شیشه‌ای
- تنظیمات بخش‌بندی‌شده، بدون اجبار به اجرای setup کامل برای هر تغییر کوچک
- مدیریت اتصال 3x-ui و API key
- مدیریت اینباندها: افزودن دستی، دریافت از 3x-ui، فعال/غیرفعال، پیش‌فرض فروش، limit IP و flow
- مدیریت پرداخت‌ها: NOWPayments، کارت‌به‌کارت، روشن/خاموش کردن روش‌ها
- فروش مستقیم سرویس با NOWPayments یا کارت‌به‌کارت
- شارژ کیف پول
- تمدید سرویس
- ساخت خودکار client در 3x-ui بعد از پرداخت
- نمایش subscription link و QR Code
- متن‌های قابل تنظیم
- هشدار نزدیک انقضا و expire خودکار
- گزارش فروش، مدیریت کاربران، ادمین و نماینده فروش
- بکاپ دیتابیس با دکمه
- تست رایگان فعلاً غیرفعال است: `TRIAL_ENABLED=false`

## نصب سریع روی سرور

```bash
cd ~
git clone https://github.com/amirinjast/telegram-3xui-shop-bot.git
cd telegram-3xui-shop-bot
cp .env.example .env
nano .env
bash install.sh
systemctl start telegram-3xui-shop-bot
journalctl -u telegram-3xui-shop-bot -f
```

داخل `.env` فقط این اجباری است:

```env
BOT_TOKEN=توکن_ربات_از_BotFather
```

بعد برو تلگرام و به ربات بزن:

```text
/start
```

اگر هنوز ادمینی در دیتابیس نباشد، اولین کسی که `/start` بزند ادمین اصلی می‌شود.

## اجرای دستی بدون systemd

```bash
cd ~/telegram-3xui-shop-bot
cp .env.example .env
nano .env
bash run_manual.sh
```

## آپدیت با یک دستور

اگر از GitHub clone کرده باشی:

```bash
cd ~/telegram-3xui-shop-bot
bash update.sh
```

یا دستی:

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart telegram-3xui-shop-bot
```

## مسیرهای مهم داخل ربات

بعد از `/start`:

```text
پنل مدیریت → تنظیمات بخش‌بندی‌شده
```

برای شماره کارت:

```text
پنل مدیریت → پرداخت‌ها → شماره کارت
```

برای NOWPayments:

```text
پنل مدیریت → پرداخت‌ها → NOWPayments API key / IPN secret
```

برای 3x-ui:

```text
پنل مدیریت → 3x-ui و اتصال
```

برای اینباندها:

```text
پنل مدیریت → اینباندها
```

برای اضافه کردن ادمین یا نماینده:

```text
پنل مدیریت → ادمین/نماینده
```

## نکته درباره 3x-ui API key

پیش‌فرض ربات این است:

```text
XUI_AUTH_HEADER=Authorization
XUI_AUTH_PREFIX=Bearer
```

اگر پنل تو `X-API-Key` می‌خواهد:

```text
پنل مدیریت → 3x-ui و اتصال → نام هدر احراز هویت → X-API-Key
پنل مدیریت → 3x-ui و اتصال → پیشوند هدر → خالی/غیرفعال
```

یا با دستور اضطراری:

```text
/set XUI_AUTH_HEADER X-API-Key
/set XUI_AUTH_PREFIX -
```

## ساخت پلن

از مسیر دکمه‌ای:

```text
پنل مدیریت → پلن‌ها → افزودن پلن
```

فرمت:

```text
نام|حجم گیگ|مدت روز|قیمت تومان|inbound_id
```

مثال:

```text
20 گیگ یک‌ماهه|20|30|150000|1
```

## NOWPayments webhook

برای پرداخت کریپتو باید `PUBLIC_BASE_URL` روی آدرس عمومی/دامنه قابل دسترس تنظیم شود. مسیر webhook:

```text
https://YOUR_DOMAIN/webhooks/nowpayments
```

## API داخلی

کلید داخلی به صورت خودکار ساخته و در SQLite ذخیره می‌شود. هدر لازم:

```text
X-API-Key: INTERNAL_API_KEY
```

Endpointها:

```text
GET  /health
POST /api/v1/wallet/credit
POST /api/v1/topup/nowpayments
POST /api/v1/buy/nowpayments
POST /webhooks/nowpayments
```

## فایل‌هایی که نباید commit شوند

این‌ها داخل `.gitignore` هستند و نباید به GitHub بروند:

```text
.env
shop.db
*.db
data/
backups/
receipts/
logs/
.venv/
```
