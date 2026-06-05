from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# ---------- User menus ----------

def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🛒 خرید سرویس", callback_data="menu:plans"),
            InlineKeyboardButton(text="💳 شارژ حساب", callback_data="menu:topup"),
        ],
        [
            InlineKeyboardButton(text="📦 سرویس‌های من", callback_data="menu:services"),
            InlineKeyboardButton(text="👤 حساب من", callback_data="menu:account"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard(plans) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{p.name} - {p.price_toman:,} تومان", callback_data=f"buy:{p.id}")]
        for p in plans
    ]
    buttons.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def pay_methods_keyboard(order_id: int, *, allow_wallet: bool = True, allow_nowpayments: bool = False, allow_manual: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if allow_wallet:
        rows.append([InlineKeyboardButton(text="💰 پرداخت از کیف پول", callback_data=f"pay_wallet:{order_id}")])
    if allow_manual:
        rows.append([InlineKeyboardButton(text="💳 کارت‌به‌کارت", callback_data=f"pay_manual:{order_id}")])
    if allow_nowpayments:
        rows.append([InlineKeyboardButton(text="💸 پرداخت NOWPayments", callback_data=f"pay_now:{order_id}")])
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="50,000 تومان", callback_data="topup_amount:50000"),
                InlineKeyboardButton(text="100,000 تومان", callback_data="topup_amount:100000"),
            ],
            [
                InlineKeyboardButton(text="150,000 تومان", callback_data="topup_amount:150000"),
                InlineKeyboardButton(text="300,000 تومان", callback_data="topup_amount:300000"),
            ],
            [InlineKeyboardButton(text="✍️ مبلغ دلخواه", callback_data="topup_custom")],
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]
    )


def topup_method_keyboard(amount_toman: int, *, allow_nowpayments: bool = False, allow_manual: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if allow_manual:
        rows.append([InlineKeyboardButton(text="💳 کارت‌به‌کارت", callback_data=f"manual_topup:{amount_toman}")])
    if allow_nowpayments:
        rows.append([InlineKeyboardButton(text="💸 NOWPayments", callback_data=f"create_now:{amount_toman}")])
    rows.append([InlineKeyboardButton(text="🔙 تغییر مبلغ", callback_data="menu:topup")])
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_receipt_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تایید", callback_data=f"adm_receipt_ok:{receipt_id}"),
                InlineKeyboardButton(text="❌ رد", callback_data=f"adm_receipt_no:{receipt_id}"),
            ]
        ]
    )


def services_keyboard(services) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in services:
        rows.append([InlineKeyboardButton(text=f"#{s.id} | {s.email} | {s.status}", callback_data=f"svc:{s.id}")])
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_detail_keyboard(service_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔗 نمایش لینک", callback_data=f"svc_link:{service_id}")],
        [InlineKeyboardButton(text="🔁 تمدید", callback_data=f"renew_same:{service_id}")],
        [InlineKeyboardButton(text="📦 سرویس‌های من", callback_data="menu:services")],
        [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]
    )


# ---------- Admin menus ----------

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 اتصال 3x-ui", callback_data="adm_cfg_xui")],
            [InlineKeyboardButton(text="🌐 اینباندها", callback_data="adm_inbounds")],
            [
                InlineKeyboardButton(text="📦 پلن‌ها", callback_data="adm_plans"),
                InlineKeyboardButton(text="➕ افزودن پلن", callback_data="adm_add_plan"),
            ],
            [InlineKeyboardButton(text="💳 پرداخت‌ها", callback_data="adm_cfg_payment")],
            [InlineKeyboardButton(text="🧾 رسیدها", callback_data="adm_pending_receipts")],
            [InlineKeyboardButton(text="👥 کاربران", callback_data="adm_users")],
            [InlineKeyboardButton(text="📤 بکاپ دیتابیس", callback_data="adm_backup")],
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")],
        ]
    )


def admin_back_keyboard(section: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if section:
        rows.append([InlineKeyboardButton(text="🔙 برگشت", callback_data=section)])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 اتصال 3x-ui", callback_data="adm_cfg_xui")],
            [InlineKeyboardButton(text="💳 پرداخت‌ها", callback_data="adm_cfg_payment")],
            [InlineKeyboardButton(text="🛒 فروش", callback_data="adm_cfg_sales")],
            [InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")],
        ]
    )


def config_section_keyboard(section: str, items: list[tuple[str, str]], *, tests: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"adm_edit:{section}:{key}")] for key, label in items]
    if section == "xui":
        rows.append([InlineKeyboardButton(text="🔌 تست و دریافت اینباند", callback_data="adm_pull_inbounds")])
    if section == "payment":
        rows.append([InlineKeyboardButton(text="روشن/خاموش کارت", callback_data="adm_toggle:MANUAL_PAYMENT_ENABLED:adm_cfg_payment")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_value_keyboard(back_callback: str, *, allow_empty: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if allow_empty:
        rows.append([InlineKeyboardButton(text="خالی/غیرفعال کن", callback_data="adm_edit_empty")])
    rows.append([InlineKeyboardButton(text="لغو", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roles_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن ادمین", callback_data="adm_role_add:admin")],
            [InlineKeyboardButton(text="➖ حذف ادمین", callback_data="adm_role_remove:admin")],
            [InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")],
        ]
    )


def users_admin_keyboard(users) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{u.telegram_id} | @{u.username or '-'}", callback_data=f"adm_user:{u.telegram_id}")] for u in users]
    rows.append([InlineKeyboardButton(text="🔎 جستجوی کاربر", callback_data="adm_user_search")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_detail_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ افزایش موجودی", callback_data=f"adm_user_balance:{telegram_id}:plus"),
                InlineKeyboardButton(text="➖ کاهش موجودی", callback_data=f"adm_user_balance:{telegram_id}:minus"),
            ],
            [InlineKeyboardButton(text="ادمین on/off", callback_data=f"adm_user_toggle_admin:{telegram_id}")],
            [InlineKeyboardButton(text="📦 سرویس‌های کاربر", callback_data=f"adm_user_services:{telegram_id}")],
            [InlineKeyboardButton(text="🔙 کاربران", callback_data="adm_users")],
        ]
    )


def inbounds_keyboard(inbounds) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for inbound in inbounds:
        mark = "⭐ " if inbound.is_default else ""
        active = "✅" if inbound.active else "⛔"
        rows.append([InlineKeyboardButton(text=f"{mark}{active} #{inbound.id} {inbound.title} / inbound={inbound.inbound_id}", callback_data=f"adm_inbound:{inbound.id}")])
    rows.append([InlineKeyboardButton(text="📥 دریافت از پنل 3x-ui", callback_data="adm_pull_inbounds")])
    rows.append([InlineKeyboardButton(text="➕ افزودن دستی", callback_data="adm_inbound_add")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inbound_detail_keyboard(inbound_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ پیش‌فرض فروش کن", callback_data=f"adm_inbound_default:{inbound_id}")],
            [InlineKeyboardButton(text="فعال/غیرفعال", callback_data=f"adm_inbound_toggle:{inbound_id}")],
            [InlineKeyboardButton(text="✏️ نام", callback_data=f"adm_inbound_edit:{inbound_id}:title")],
            [InlineKeyboardButton(text="✏️ limit IP", callback_data=f"adm_inbound_edit:{inbound_id}:limit_ip")],
            [InlineKeyboardButton(text="✏️ flow", callback_data=f"adm_inbound_edit:{inbound_id}:flow")],
            [InlineKeyboardButton(text="🔙 اینباندها", callback_data="adm_inbounds")],
        ]
    )


def setup_step_keyboard(step: dict, index: int, total: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    quick: list[InlineKeyboardButton] = []
    if step.get("default") is not None:
        quick.append(InlineKeyboardButton(text="استفاده از پیش‌فرض", callback_data="setup:default"))
    if step.get("same_as"):
        quick.append(InlineKeyboardButton(text="همان آدرس پنل", callback_data="setup:same"))
    if step.get("dash_empty"):
        quick.append(InlineKeyboardButton(text="خالی/بدون پیشوند", callback_data="setup:empty"))
    if quick:
        for i in range(0, len(quick), 2):
            buttons.append(quick[i : i + 2])
    if step.get("allow_skip") or step.get("key") == "PLAN":
        buttons.append([InlineKeyboardButton(text="رد کردن / نگه‌داشتن قبلی", callback_data="setup:skip")])
    nav: list[InlineKeyboardButton] = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="⬅️ مرحله قبل", callback_data="setup:back"))
    nav.append(InlineKeyboardButton(text="لغو", callback_data="setup:cancel"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(text=f"مرحله {index + 1}/{total}", callback_data="setup:noop")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 تست 3x-ui", callback_data="adm_test_xui")],
            [InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")],
        ]
    )


def plans_admin_keyboard(plans) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in plans:
        status = "غیرفعال" if p.active else "فعال"
        rows.append([
            InlineKeyboardButton(text=f"#{p.id} {p.name}", callback_data=f"adm_plan_view:{p.id}"),
            InlineKeyboardButton(text=f"تبدیل به {status}", callback_data=f"adm_plan_toggle:{p.id}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن پلن", callback_data="adm_add_plan")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_detail_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ نام", callback_data=f"adm_plan_edit:{plan_id}:name")],
            [
                InlineKeyboardButton(text="✏️ حجم", callback_data=f"adm_plan_edit:{plan_id}:traffic_gb"),
                InlineKeyboardButton(text="✏️ مدت", callback_data=f"adm_plan_edit:{plan_id}:days"),
            ],
            [
                InlineKeyboardButton(text="✏️ قیمت", callback_data=f"adm_plan_edit:{plan_id}:price_toman"),
                InlineKeyboardButton(text="✏️ inbound", callback_data=f"adm_plan_edit:{plan_id}:xui_inbound_id"),
            ],
            [InlineKeyboardButton(text="فعال/غیرفعال", callback_data=f"adm_plan_toggle:{plan_id}")],
            [InlineKeyboardButton(text="🔙 پلن‌ها", callback_data="adm_plans")],
        ]
    )


def texts_keyboard(keys: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=key, callback_data=f"adm_text_edit:{key}")] for key in keys]
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
