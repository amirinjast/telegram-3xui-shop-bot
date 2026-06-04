from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, select

from app.bot.keyboards import (
    admin_back_keyboard,
    admin_panel_keyboard,
    config_section_keyboard,
    edit_value_keyboard,
    inbound_detail_keyboard,
    inbounds_keyboard,
    main_menu,
    plan_detail_keyboard,
    plans_admin_keyboard,
    roles_keyboard,
    settings_hub_keyboard,
    settings_keyboard,
    setup_step_keyboard,
    texts_keyboard,
    user_detail_keyboard,
    users_admin_keyboard,
)
from app.config import get_settings, reload_settings
from app.db import session_scope
from app.messages import TEXTS, get_text, set_text
from app.models import ManualReceipt, Order, Payment, Plan, Service, Transaction, User, XuiInbound
from app.panels.xui import XUIClient, XUIError
from app.payments.nowpayments import NowPaymentsClient, NowPaymentsError
from app.pricing import fmt_toman
from app.runtime_config import mask_value, update_runtime_config
from app.services.accounts import get_or_create_user, get_user_by_telegram_id
from app.services.billing import approve_manual_receipt, reject_manual_receipt

router = Router()


class SetupStates(StatesGroup):
    wizard = State()


class AddPlanStates(StatesGroup):
    waiting_payload = State()


class EditTextStates(StatesGroup):
    waiting_text = State()


class EditConfigStates(StatesGroup):
    waiting_value = State()


class RoleStates(StatesGroup):
    waiting_user_id = State()


class UserAdminStates(StatesGroup):
    waiting_search = State()
    waiting_balance = State()


class InboundStates(StatesGroup):
    waiting_payload = State()
    waiting_edit = State()


class EditPlanStates(StatesGroup):
    waiting_value = State()


SETUP_STEPS: list[dict[str, Any]] = [
    {"key": "XUI_BASE_URL", "title": "آدرس پنل 3x-ui", "hint": "مثال: https://panel.example.com یا https://1.2.3.4:2053\nبدون / آخر بفرست."},
    {"key": "XUI_API_KEY", "title": "API key پنل 3x-ui", "hint": "همان کلیدی که پنل بهت داده. پیام بعد از ذخیره ماسک می‌شود.", "secret": True},
    {"key": "XUI_AUTH_HEADER", "title": "نام هدر احراز هویت 3x-ui", "hint": "معمولاً Authorization است. اگر پنلت X-API-Key می‌خواهد همان را بفرست.", "default": "Authorization"},
    {"key": "XUI_AUTH_PREFIX", "title": "پیشوند هدر 3x-ui", "hint": "معمولاً Bearer است. اگر پنلت فقط خود key را می‌خواهد، فقط - بفرست.", "default": "Bearer", "dash_empty": True},
    {"key": "XUI_API_PREFIX", "title": "مسیر API پنل", "hint": "معمولاً /panel/api است.", "default": "/panel/api"},
    {"key": "XUI_SUBSCRIPTION_BASE_URL", "title": "آدرس لینک subscription", "hint": "اگر همان آدرس پنل است، بنویس same. مثال خروجی: https://domain.com/sub/xxxx", "same_as": "XUI_BASE_URL"},
    {"key": "XUI_DEFAULT_INBOUND_ID", "title": "آیدی inbound پیش‌فرض", "hint": "معمولاً 1 است. فقط عدد بفرست.", "default": 1, "type": "int"},
    {"key": "USD_RATE_TOMAN", "title": "نرخ ثابت دلار به تومان", "hint": "مثلاً اگر دلار را 150 هزار حساب می‌کنی، بفرست: 150000", "default": 150000, "type": "int"},
    {"key": "PUBLIC_BASE_URL", "title": "آدرس عمومی ربات/API", "hint": "برای callback پرداخت لازم است. بهتر: https://bot.example.com\nبرای تست بدون دامنه: http://SERVER_IP:8080", "default": "http://localhost:8080"},
    {"key": "NOWPAYMENTS_API_KEY", "title": "API key درگاه NOWPayments", "hint": "اگر فعلاً نمی‌خواهی کریپتو فعال باشد، skip بفرست.", "secret": True, "allow_skip": True},
    {"key": "NOWPAYMENTS_IPN_SECRET", "title": "IPN secret در NOWPayments", "hint": "باید با IPN secret داخل اکانت NOWPayments یکی باشد. اگر فعلاً نداری، skip بفرست.", "secret": True, "allow_skip": True},
    {"key": "NOWPAYMENTS_PAY_CURRENCY", "title": "ارز پرداخت پیش‌فرض NOWPayments", "hint": "برای انتخاب آزاد توسط کاربر فقط - بفرست. برای USDT TRC20 بفرست: usdttrc20", "dash_empty": True},
    {"key": "CARD_TO_CARD_NUMBER", "title": "شماره کارت کارت‌به‌کارت", "hint": "اگر فعلاً نمی‌خواهی کارت‌به‌کارت فعال باشد، skip بفرست.", "allow_skip": True},
    {"key": "CARD_TO_CARD_OWNER", "title": "نام صاحب کارت", "hint": "مثال: Amir Akhondi. اگر قبلی را نگه می‌داری، skip بفرست.", "allow_skip": True},
    {"key": "PLAN", "title": "اضافه کردن اولین پلن", "hint": "فرمت: نام|حجم گیگ|مدت روز|قیمت تومان|inbound\nمثال: 20 گیگ یک‌ماهه|20|30|150000|1\nاگر بعداً می‌خواهی اضافه کنی، skip بفرست.", "allow_skip": True},
]

CONFIG_KEYS = {step["key"] for step in SETUP_STEPS if step["key"] != "PLAN"} | {
    "ADMIN_IDS",
    "INTERNAL_API_KEY",
    "MIN_TOPUP_TOMAN",
    "NOWPAYMENTS_ENABLED",
    "MANUAL_PAYMENT_ENABLED",
    "NOWPAYMENTS_SUCCESS_STATUSES",
    "TRIAL_ENABLED",
    "EXPIRE_WARNING_CHECK_HOURS",
    "XUI_DEFAULT_LIMIT_IP",
    "XUI_DEFAULT_FLOW",
}

CONFIG_SECTIONS: dict[str, list[tuple[str, str]]] = {
    "xui": [
        ("XUI_BASE_URL", "آدرس پنل 3x-ui"),
        ("XUI_API_KEY", "API key پنل"),
        ("XUI_AUTH_HEADER", "نام هدر احراز هویت"),
        ("XUI_AUTH_PREFIX", "پیشوند هدر: Bearer یا خالی"),
        ("XUI_API_PREFIX", "مسیر API پنل"),
        ("XUI_SUBSCRIPTION_BASE_URL", "آدرس subscription"),
    ],
    "payment": [
        ("PUBLIC_BASE_URL", "آدرس عمومی ربات/API"),
        ("NOWPAYMENTS_API_KEY", "NOWPayments API key"),
        ("NOWPAYMENTS_IPN_SECRET", "NOWPayments IPN secret"),
        ("NOWPAYMENTS_PAY_CURRENCY", "ارز پرداخت پیش‌فرض"),
        ("NOWPAYMENTS_SUCCESS_STATUSES", "وضعیت‌های موفق پرداخت"),
        ("CARD_TO_CARD_NUMBER", "شماره کارت"),
        ("CARD_TO_CARD_OWNER", "نام صاحب کارت"),
    ],
    "sales": [
        ("USD_RATE_TOMAN", "نرخ دلار به تومان"),
        ("MIN_TOPUP_TOMAN", "حداقل شارژ کیف پول"),
        ("XUI_DEFAULT_INBOUND_ID", "inbound پیش‌فرض فروش"),
        ("XUI_DEFAULT_LIMIT_IP", "limit IP پیش‌فرض"),
        ("XUI_DEFAULT_FLOW", "flow پیش‌فرض"),
        ("EXPIRE_WARNING_CHECK_HOURS", "فاصله چک هشدار انقضا"),
    ],
}

CONFIG_SECTION_TITLES = {
    "xui": "🔌 اتصال 3x-ui",
    "payment": "💳 تنظیمات پرداخت",
    "sales": "🛒 تنظیمات فروش و پیش‌فرض‌ها",
}

CONFIG_VALUE_META = {
    "XUI_API_KEY": {"secret": True},
    "NOWPAYMENTS_API_KEY": {"secret": True},
    "NOWPAYMENTS_IPN_SECRET": {"secret": True},
    "INTERNAL_API_KEY": {"secret": True},
    "USD_RATE_TOMAN": {"type": "int"},
    "MIN_TOPUP_TOMAN": {"type": "int"},
    "XUI_DEFAULT_INBOUND_ID": {"type": "int"},
    "XUI_DEFAULT_LIMIT_IP": {"type": "int"},
    "EXPIRE_WARNING_CHECK_HOURS": {"type": "int"},
    "NOWPAYMENTS_ENABLED": {"type": "bool"},
    "MANUAL_PAYMENT_ENABLED": {"type": "bool"},
    "TRIAL_ENABLED": {"type": "bool"},
}


async def _is_admin(telegram_user) -> bool:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        return user.is_admin


def _clean_text(text: str) -> str:
    return (text or "").strip().replace("\u200c", " ").strip()


def _current_value_for_prompt(key: str) -> str:
    settings = get_settings()
    if key == "PLAN":
        return "پلن دیتابیسی"
    attr = {
        "XUI_BASE_URL": "xui_base_url",
        "XUI_API_KEY": "xui_api_key",
        "XUI_AUTH_HEADER": "xui_auth_header",
        "XUI_AUTH_PREFIX": "xui_auth_prefix",
        "XUI_API_PREFIX": "xui_api_prefix",
        "XUI_SUBSCRIPTION_BASE_URL": "xui_subscription_base_url",
        "XUI_DEFAULT_INBOUND_ID": "xui_default_inbound_id",
        "XUI_DEFAULT_LIMIT_IP": "xui_default_limit_ip",
        "XUI_DEFAULT_FLOW": "xui_default_flow",
        "USD_RATE_TOMAN": "usd_rate_toman",
        "MIN_TOPUP_TOMAN": "min_topup_toman",
        "EXPIRE_WARNING_CHECK_HOURS": "expire_warning_hours",
        "PUBLIC_BASE_URL": "public_base_url",
        "NOWPAYMENTS_ENABLED": "nowpayments_enabled",
        "MANUAL_PAYMENT_ENABLED": "manual_payment_enabled",
        "NOWPAYMENTS_API_KEY": "nowpayments_api_key",
        "NOWPAYMENTS_IPN_SECRET": "nowpayments_ipn_secret",
        "NOWPAYMENTS_PAY_CURRENCY": "nowpayments_pay_currency",
        "NOWPAYMENTS_SUCCESS_STATUSES": "nowpayments_success_statuses_raw",
        "CARD_TO_CARD_NUMBER": "card_to_card_number",
        "CARD_TO_CARD_OWNER": "card_to_card_owner",
        "TRIAL_ENABLED": "trial_enabled",
    }.get(key)
    value = getattr(settings, attr, "") if attr else ""
    if key in {"XUI_API_KEY", "NOWPAYMENTS_API_KEY", "NOWPAYMENTS_IPN_SECRET"}:
        return mask_value(value)
    return str(value) if str(value) else "تنظیم نشده"


def _setup_prompt(index: int) -> str:
    step = SETUP_STEPS[index]
    key = step["key"]
    skip_text = "\nبرای رد کردن/نگه‌داشتن مقدار قبلی: <code>skip</code>" if step.get("allow_skip") else ""
    default = step.get("default")
    default_text = f"\nپیش‌فرض پیشنهادی: <code>{html.escape(str(default))}</code>" if default is not None else ""
    return (
        f"مرحله {index + 1}/{len(SETUP_STEPS)}\n"
        f"<b>{html.escape(step['title'])}</b>\n\n"
        f"{html.escape(step['hint'])}"
        f"\n\nمقدار فعلی: <code>{html.escape(_current_value_for_prompt(key))}</code>"
        f"{default_text}{skip_text}"
    )


async def _send_setup_step(target: Message, index: int) -> None:
    step = SETUP_STEPS[index]
    await target.answer(_setup_prompt(index), reply_markup=setup_step_keyboard(step, index, len(SETUP_STEPS)))


async def _advance_setup(target: Message, state: FSMContext, index: int, raw_value: str) -> None:
    step = SETUP_STEPS[index]
    ok, msg = await _save_config_value(step["key"], raw_value, state)
    if not ok:
        await target.answer(msg)
        await _send_setup_step(target, index)
        return
    await target.answer(msg)
    next_index = index + 1
    if next_index >= len(SETUP_STEPS):
        await state.clear()
        await target.answer("✅ تنظیمات اصلی ذخیره شد. تست اتصال بگیر:", reply_markup=admin_panel_keyboard())
        await target.answer(_settings_text(), reply_markup=settings_keyboard())
        return
    await state.update_data(step_index=next_index)
    await _send_setup_step(target, next_index)


def _settings_text() -> str:
    settings = get_settings()
    rows = [
        ("3x-ui URL", settings.xui_base_url or "تنظیم نشده"),
        ("3x-ui API key", mask_value(settings.xui_api_key)),
        ("3x-ui header", f"{settings.xui_auth_header}: {settings.xui_auth_prefix} ***".replace("  ", " ")),
        ("3x-ui API prefix", settings.xui_api_prefix),
        ("Subscription base", settings.xui_subscription_base_url or settings.xui_base_url or "تنظیم نشده"),
        ("Default inbound", settings.xui_default_inbound_id),
        ("Dollar rate", f"{settings.usd_rate_toman:,} تومان"),
        ("Public URL", settings.public_base_url),
        ("NOWPayments key", mask_value(settings.nowpayments_api_key)),
        ("NOWPayments IPN", mask_value(settings.nowpayments_ipn_secret)),
        ("NOWPayments pay currency", settings.nowpayments_pay_currency or "انتخاب توسط کاربر"),
        ("Card number", settings.card_to_card_number or "تنظیم نشده"),
        ("Card owner", settings.card_to_card_owner or "تنظیم نشده"),
        ("Trial", "غیرفعال" if not settings.trial_enabled else "فعال"),
        ("Internal API key", mask_value(settings.internal_api_key)),
    ]
    body = "\n".join(f"• <b>{html.escape(str(k))}</b>: <code>{html.escape(str(v))}</code>" for k, v in rows)
    return body + "\n\nبرای تغییر، از دکمه‌ها استفاده کن. تست رایگان فعلاً پیش‌فرض غیرفعال است."


async def _save_config_value(key: str, raw_value: str, state: FSMContext | None = None) -> tuple[bool, str]:
    del state
    text = _clean_text(raw_value)
    step = next((s for s in SETUP_STEPS if s["key"] == key), {})
    lower = text.lower()

    if lower in {"skip", "رد", "بعدا", "بعداً"}:
        if step.get("allow_skip") or key == "PLAN":
            return True, "رد شد و مقدار قبلی حفظ شد."
        return False, "این مرحله ضروری است؛ مقدار را بفرست."

    if text == "-" and key in {"XUI_AUTH_PREFIX", "XUI_SUBSCRIPTION_BASE_URL", "NOWPAYMENTS_PAY_CURRENCY", "CARD_TO_CARD_NUMBER", "CARD_TO_CARD_OWNER", "XUI_DEFAULT_FLOW"}:
        update_runtime_config({key: ""})
        reload_settings()
        return True, f"ذخیره شد: {key} = خالی"

    if key in {"NOWPAYMENTS_ENABLED", "MANUAL_PAYMENT_ENABLED", "TRIAL_ENABLED"}:
        if lower in {"1", "true", "on", "yes", "فعال"}:
            value = True
        elif lower in {"0", "false", "off", "no", "غیرفعال"}:
            value = False
        else:
            return False, "برای این گزینه بفرست: true یا false"
        update_runtime_config({key: value})
        reload_settings()
        return True, f"ذخیره شد: {key} = {value}"

    if key in {"MIN_TOPUP_TOMAN", "EXPIRE_WARNING_CHECK_HOURS", "XUI_DEFAULT_INBOUND_ID", "XUI_DEFAULT_LIMIT_IP", "USD_RATE_TOMAN"}:
        numeric = text.replace(",", "")
        if not numeric.isdigit():
            return False, "فقط عدد بفرست."
        value = int(numeric)
        update_runtime_config({key: value})
        reload_settings()
        return True, f"ذخیره شد: {key} = {value}"

    if key in {"ADMIN_IDS", "NOWPAYMENTS_SUCCESS_STATUSES", "INTERNAL_API_KEY"}:
        update_runtime_config({key: text})
        reload_settings()
        shown = mask_value(text) if key in {"INTERNAL_API_KEY"} else text
        return True, f"ذخیره شد: {key} = {shown}"

    if key == "PLAN":
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 4:
            return False, "فرمت پلن درست نیست. مثال: 20 گیگ یک‌ماهه|20|30|150000 یا 20 گیگ یک‌ماهه|20|30|150000|1"
        name, traffic, days, price = parts[:4]
        inbound = parts[4] if len(parts) >= 5 and parts[4] else str(get_settings().xui_default_inbound_id)
        if not (traffic.isdigit() and days.isdigit() and price.replace(",", "").isdigit() and inbound.isdigit()):
            return False, "حجم، روز، قیمت و inbound باید عدد باشند."
        settings = get_settings()
        async with session_scope() as session:
            plan = Plan(name=name, traffic_gb=int(traffic), days=int(days), price_toman=int(price.replace(",", "")), xui_inbound_id=int(inbound), xui_limit_ip=settings.xui_default_limit_ip, xui_flow=settings.xui_default_flow or "", active=True)
            session.add(plan)
            await session.flush()
            plan_id = plan.id
        return True, f"پلن #{plan_id} اضافه شد."

    if step.get("dash_empty") and text == "-":
        value: Any = ""
    elif step.get("same_as") and lower in {"same", "همان", "همین"}:
        settings = get_settings()
        value = getattr(settings, "xui_base_url")
    elif step.get("type") == "int":
        numeric = text.replace(",", "")
        if not numeric.isdigit():
            return False, "فقط عدد بفرست."
        value = int(numeric)
    else:
        value = text

    if key not in CONFIG_KEYS:
        return False, "این کلید قابل تنظیم نیست."

    update_runtime_config({key: value})
    reload_settings()
    shown = mask_value(value) if step.get("secret") else ("خالی" if value == "" else str(value))
    return True, f"ذخیره شد: {key} = {shown}"



def _config_section_text(section: str) -> str:
    title = CONFIG_SECTION_TITLES.get(section, "⚙️ تنظیمات")
    lines = [f"<b>{html.escape(title)}</b>", ""]
    for key, label in CONFIG_SECTIONS.get(section, []):
        current = _current_value_for_prompt(key)
        lines.append(f"• <b>{html.escape(label)}</b>\n  <code>{html.escape(current)}</code>")
    if section == "payment":
        lines.append("")
        lines.append(f"• NOWPayments: <code>{_current_value_for_prompt('NOWPAYMENTS_ENABLED')}</code>")
        lines.append(f"• کارت‌به‌کارت: <code>{_current_value_for_prompt('MANUAL_PAYMENT_ENABLED')}</code>")
    if section == "sales":
        lines.append("")
        lines.append("• تست رایگان: <code>غیرفعال</code>" if not get_settings().trial_enabled else "• تست رایگان: <code>فعال</code>")
    lines.append("\nبرای تغییر فقط همان گزینه را بزن؛ دیگر لازم نیست کل setup را از اول بروی.")
    return "\n".join(lines)


def _edit_prompt(section: str, key: str) -> str:
    label = next((label for item_key, label in CONFIG_SECTIONS.get(section, []) if item_key == key), key)
    current = _current_value_for_prompt(key)
    tips = {
        "XUI_BASE_URL": "مثال: https://panel.example.com",
        "XUI_API_KEY": "API key پنل 3x-ui را بفرست.",
        "XUI_AUTH_HEADER": "مثلاً Authorization یا X-API-Key",
        "XUI_AUTH_PREFIX": "مثلاً Bearer. اگر prefix نمی‌خواهی، دکمه خالی/غیرفعال را بزن.",
        "XUI_API_PREFIX": "معمولاً /panel/api",
        "XUI_SUBSCRIPTION_BASE_URL": "مثلاً https://panel.example.com یا دامنه subscription",
        "PUBLIC_BASE_URL": "برای webhook پرداخت. بهتر است https://bot.example.com باشد.",
        "NOWPAYMENTS_PAY_CURRENCY": "خالی = کاربر ارز را انتخاب کند. مثال ثابت: usdttrc20",
        "NOWPAYMENTS_SUCCESS_STATUSES": "مثال: finished,confirmed",
        "USD_RATE_TOMAN": "فقط عدد. مثال: 150000",
        "MIN_TOPUP_TOMAN": "فقط عدد. مثال: 50000",
        "XUI_DEFAULT_INBOUND_ID": "فقط عدد inbound داخل 3x-ui. از بخش اینباندها هم می‌توانی پیش‌فرض کنی.",
        "XUI_DEFAULT_LIMIT_IP": "فقط عدد؛ 0 یعنی بدون محدودیت.",
        "EXPIRE_WARNING_CHECK_HOURS": "هر چند ساعت یک‌بار سرویس‌ها چک شوند. مثال: 6",
    }
    tip = tips.get(key, "مقدار جدید را بفرست.")
    return (
        f"✏️ تغییر <b>{html.escape(label)}</b>\n\n"
        f"کلید: <code>{html.escape(key)}</code>\n"
        f"مقدار فعلی: <code>{html.escape(current)}</code>\n\n"
        f"{html.escape(tip)}\n\n"
        "برای لغو، دکمه برگشت را بزن."
    )


async def _send_config_section(message: Message, section: str) -> None:
    await message.answer(
        _config_section_text(section),
        reply_markup=config_section_keyboard(section, CONFIG_SECTIONS.get(section, [])),
    )


async def _send_inbounds(message: Message) -> None:
    async with session_scope() as session:
        inbounds = (await session.execute(select(XuiInbound).order_by(XuiInbound.is_default.desc(), XuiInbound.id))).scalars().all()
    if not inbounds:
        await message.answer(
            "🌐 هنوز هیچ inboundی داخل دیتابیس ربات ثبت نشده.\n\n"
            "می‌توانی دستی اضافه کنی یا از پنل 3x-ui دریافت کنی.",
            reply_markup=inbounds_keyboard([]),
        )
        return
    await message.answer("🌐 مدیریت اینباندها\n⭐ یعنی پیش‌فرض فروش", reply_markup=inbounds_keyboard(inbounds))


async def _send_user_detail(message: Message, telegram_id: int) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await message.answer("کاربر پیدا نشد.", reply_markup=admin_back_keyboard("adm_users"))
            return
        services_count = (await session.execute(select(func.count(Service.id)).where(Service.user_id == user.id))).scalar() or 0
        orders_count = (await session.execute(select(func.count(Order.id)).where(Order.user_id == user.id))).scalar() or 0
    role = "ادمین" if user.is_admin else ("نماینده" if getattr(user, "is_reseller", False) else "کاربر")
    text = (
        f"👤 کاربر <code>{user.telegram_id}</code>\n"
        f"یوزرنیم: @{html.escape(user.username or '-')}\n"
        f"نام: {html.escape(user.first_name or '-')}\n"
        f"نقش: {role}\n"
        f"موجودی: {fmt_toman(user.balance_toman)}\n"
        f"سرویس‌ها: {services_count}\n"
        f"سفارش‌ها: {orders_count}"
    )
    await message.answer(text, reply_markup=user_detail_keyboard(telegram_id))


@router.callback_query(F.data == "adm_settings_hub")
async def admin_settings_hub(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await callback.message.answer(
        "⚙️ تنظیمات بخش‌بندی‌شده\n\nهر بخش مستقل است؛ لازم نیست برای تغییر یک چیز کوچک، همه مراحل راه‌اندازی را بروی.",
        reply_markup=settings_hub_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"adm_cfg_xui", "adm_cfg_payment", "adm_cfg_sales"}))
async def admin_config_section(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    section = callback.data.replace("adm_cfg_", "")
    await _send_config_section(callback.message, section)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_edit:"))
async def admin_edit_config_start(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    _, section, key = callback.data.split(":", 2)
    if key not in CONFIG_KEYS or section not in CONFIG_SECTIONS:
        await callback.answer("این گزینه قابل تغییر نیست.", show_alert=True)
        return
    await state.set_state(EditConfigStates.waiting_value)
    await state.update_data(config_key=key, config_section=section)
    await callback.message.answer(_edit_prompt(section, key), reply_markup=edit_value_keyboard(f"adm_cfg_{section}"))
    await callback.answer()


@router.callback_query(EditConfigStates.waiting_value, F.data == "adm_edit_empty")
async def admin_edit_config_empty(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await state.clear()
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    data = await state.get_data()
    key = data.get("config_key")
    section = data.get("config_section", "xui")
    ok, msg = await _save_config_value(str(key), "-")
    await state.clear()
    await callback.message.answer(msg)
    await _send_config_section(callback.message, str(section))
    await callback.answer()


@router.message(EditConfigStates.waiting_value)
async def admin_edit_config_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    data = await state.get_data()
    key = str(data.get("config_key"))
    section = str(data.get("config_section", "xui"))
    ok, msg = await _save_config_value(key, message.text or "")
    await message.answer(msg)
    if ok:
        await state.clear()
        await _send_config_section(message, section)
    else:
        await message.answer(_edit_prompt(section, key), reply_markup=edit_value_keyboard(f"adm_cfg_{section}"))


@router.callback_query(F.data.startswith("adm_toggle:"))
async def admin_toggle_setting(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    _, key, back = callback.data.split(":", 2)
    settings = get_settings()
    attr = {
        "NOWPAYMENTS_ENABLED": "nowpayments_enabled",
        "MANUAL_PAYMENT_ENABLED": "manual_payment_enabled",
        "TRIAL_ENABLED": "trial_enabled",
    }.get(key)
    if not attr:
        await callback.answer("گزینه نامعتبر است.", show_alert=True)
        return
    new_value = not bool(getattr(settings, attr))
    update_runtime_config({key: new_value})
    reload_settings()
    await callback.message.answer(f"✅ {key} شد: {'فعال' if new_value else 'غیرفعال'}")
    if back.startswith("adm_cfg_"):
        await _send_config_section(callback.message, back.replace("adm_cfg_", ""))
    await callback.answer()


@router.callback_query(F.data == "adm_roles")
async def admin_roles(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    async with session_scope() as session:
        admins = (await session.execute(select(User).where(User.is_admin == True).order_by(User.id))).scalars().all()  # noqa: E712
        resellers = (await session.execute(select(User).where(User.is_reseller == True).order_by(User.id))).scalars().all()  # noqa: E712
    a = "\n".join(f"• <code>{u.telegram_id}</code> @{html.escape(u.username or '-')}" for u in admins) or "نداریم"
    r = "\n".join(f"• <code>{u.telegram_id}</code> @{html.escape(u.username or '-')}" for u in resellers) or "نداریم"
    await callback.message.answer(f"🧑‍💼 مدیریت ادمین و نماینده\n\nادمین‌ها:\n{a}\n\nنماینده‌ها:\n{r}", reply_markup=roles_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("adm_role_add:") | F.data.startswith("adm_role_remove:"))
async def admin_role_start(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    action, role = callback.data.replace("adm_role_", "").split(":", 1)
    await state.set_state(RoleStates.waiting_user_id)
    await state.update_data(role_action=action, role=role)
    role_fa = "ادمین" if role == "admin" else "نماینده"
    act_fa = "اضافه کردن" if action == "add" else "حذف کردن"
    await callback.message.answer(f"برای {act_fa} {role_fa}، آیدی عددی تلگرام کاربر را بفرست.\nبرای لغو: cancel", reply_markup=admin_back_keyboard("adm_roles"))
    await callback.answer()


@router.message(RoleStates.waiting_user_id)
async def admin_role_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=roles_keyboard())
        return
    if not text.isdigit():
        await message.answer("فقط آیدی عددی تلگرام را بفرست.")
        return
    data = await state.get_data()
    action = data.get("role_action")
    role = data.get("role")
    tg_id = int(text)
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            user = User(telegram_id=tg_id)
            session.add(user)
        value = action == "add"
        if role == "admin":
            user.is_admin = value
        else:
            user.is_reseller = value
    await state.clear()
    role_fa = "ادمین" if role == "admin" else "نماینده"
    await message.answer(f"✅ نقش {role_fa} برای <code>{tg_id}</code> {'فعال' if action == 'add' else 'حذف'} شد.", reply_markup=roles_keyboard())


@router.callback_query(F.data == "adm_inbounds")
async def admin_inbounds(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await _send_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data == "adm_inbound_add")
async def admin_inbound_add(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await state.set_state(InboundStates.waiting_payload)
    await callback.message.answer(
        "➕ افزودن اینباند دستی\n\nفرمت:\n<code>نام|inbound_id|limit_ip|flow</code>\n\nمثال:\n<code>آلمان ۱|1|0|-</code>\nبرای لغو: cancel",
        reply_markup=admin_back_keyboard("adm_inbounds"),
    )
    await callback.answer()


@router.message(InboundStates.waiting_payload)
async def admin_inbound_add_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=admin_back_keyboard("adm_inbounds"))
        return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("فرمت درست نیست. مثال: آلمان ۱|1|0|-")
        return
    title = parts[0]
    inbound_num = int(parts[1])
    limit_ip = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    flow = "" if len(parts) <= 3 or parts[3] == "-" else parts[3]
    async with session_scope() as session:
        has_default = (await session.execute(select(func.count(XuiInbound.id)).where(XuiInbound.is_default == True))).scalar() or 0  # noqa: E712
        inbound = XuiInbound(title=title, inbound_id=inbound_num, limit_ip=limit_ip, flow=flow, is_default=not bool(has_default))
        session.add(inbound)
        if inbound.is_default:
            update_runtime_config({"XUI_DEFAULT_INBOUND_ID": inbound_num, "XUI_DEFAULT_LIMIT_IP": limit_ip, "XUI_DEFAULT_FLOW": flow})
            reload_settings()
    await state.clear()
    await message.answer("✅ اینباند اضافه شد.")
    await _send_inbounds(message)


@router.callback_query(F.data.startswith("adm_inbound:"))
async def admin_inbound_view(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    db_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        inbound = await session.get(XuiInbound, db_id)
    if not inbound:
        await callback.answer("اینباند پیدا نشد.", show_alert=True)
        return
    text = (
        f"🌐 اینباند #{inbound.id}\n"
        f"نام: <b>{html.escape(inbound.title)}</b>\n"
        f"inbound_id پنل: <code>{inbound.inbound_id}</code>\n"
        f"وضعیت: {'فعال' if inbound.active else 'غیرفعال'}\n"
        f"پیش‌فرض فروش: {'بله' if inbound.is_default else 'خیر'}\n"
        f"limit IP: {inbound.limit_ip}\n"
        f"flow: <code>{html.escape(inbound.flow or '-')}</code>"
    )
    await callback.message.answer(text, reply_markup=inbound_detail_keyboard(db_id))
    await callback.answer()


@router.callback_query(F.data.startswith("adm_inbound_default:"))
async def admin_inbound_default(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    db_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        inbound = await session.get(XuiInbound, db_id)
        if not inbound:
            await callback.answer("اینباند پیدا نشد.", show_alert=True)
            return
        all_inbounds = (await session.execute(select(XuiInbound))).scalars().all()
        for item in all_inbounds:
            item.is_default = item.id == inbound.id
        update_runtime_config({
            "XUI_DEFAULT_INBOUND_ID": inbound.inbound_id,
            "XUI_DEFAULT_LIMIT_IP": inbound.limit_ip,
            "XUI_DEFAULT_FLOW": inbound.flow or "",
        })
        reload_settings()
    await callback.message.answer("✅ اینباند پیش‌فرض فروش شد.")
    await _send_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_inbound_toggle:"))
async def admin_inbound_toggle(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    db_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        inbound = await session.get(XuiInbound, db_id)
        if not inbound:
            await callback.answer("اینباند پیدا نشد.", show_alert=True)
            return
        inbound.active = not inbound.active
    await callback.message.answer("✅ وضعیت اینباند تغییر کرد.")
    await _send_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_inbound_edit:"))
async def admin_inbound_edit(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    _, db_id, field = callback.data.replace("adm_inbound_edit:", "").split(":", 2)
    await state.set_state(InboundStates.waiting_edit)
    await state.update_data(inbound_db_id=int(db_id), inbound_field=field)
    await callback.message.answer(f"مقدار جدید برای <code>{html.escape(field)}</code> را بفرست. برای flow خالی، - بفرست.", reply_markup=admin_back_keyboard("adm_inbounds"))
    await callback.answer()


@router.message(InboundStates.waiting_edit)
async def admin_inbound_edit_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    data = await state.get_data()
    db_id = int(data.get("inbound_db_id"))
    field = str(data.get("inbound_field"))
    value = (message.text or "").strip()
    async with session_scope() as session:
        inbound = await session.get(XuiInbound, db_id)
        if not inbound:
            await message.answer("اینباند پیدا نشد.")
            await state.clear()
            return
        if field == "title":
            inbound.title = value
        elif field == "limit_ip":
            if not value.isdigit():
                await message.answer("limit_ip باید عدد باشد.")
                return
            inbound.limit_ip = int(value)
        elif field == "flow":
            inbound.flow = "" if value == "-" else value
        if inbound.is_default:
            update_runtime_config({"XUI_DEFAULT_INBOUND_ID": inbound.inbound_id, "XUI_DEFAULT_LIMIT_IP": inbound.limit_ip, "XUI_DEFAULT_FLOW": inbound.flow or ""})
            reload_settings()
    await state.clear()
    await message.answer("✅ اینباند ذخیره شد.")
    await _send_inbounds(message)


@router.callback_query(F.data == "adm_pull_inbounds")
async def admin_pull_inbounds(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    try:
        data = await XUIClient().list_inbounds()
    except Exception as exc:
        await callback.message.answer(f"❌ دریافت اینباند از 3x-ui ناموفق بود:\n<code>{html.escape(str(exc))}</code>", reply_markup=admin_back_keyboard("adm_cfg_xui"))
        await callback.answer()
        return
    raw = []
    if isinstance(data, dict):
        obj = data.get("obj") or data.get("data") or data.get("inbounds")
        if isinstance(obj, list):
            raw = obj
    added = 0
    async with session_scope() as session:
        existing_ids = {x for (x,) in (await session.execute(select(XuiInbound.inbound_id))).all()}
        has_default = (await session.execute(select(func.count(XuiInbound.id)).where(XuiInbound.is_default == True))).scalar() or 0  # noqa: E712
        for item in raw:
            if not isinstance(item, dict):
                continue
            inbound_num = item.get("id") or item.get("inbound_id")
            if not isinstance(inbound_num, int) or inbound_num in existing_ids:
                continue
            title = item.get("remark") or item.get("tag") or item.get("protocol") or f"Inbound {inbound_num}"
            inbound = XuiInbound(title=str(title), inbound_id=inbound_num, is_default=(not has_default and added == 0))
            session.add(inbound)
            added += 1
            if inbound.is_default:
                update_runtime_config({"XUI_DEFAULT_INBOUND_ID": inbound_num})
                reload_settings()
    await callback.message.answer(f"✅ دریافت تمام شد. {added} اینباند جدید اضافه شد.")
    await _send_inbounds(callback.message)
    await callback.answer()


@router.message(F.text == "🛠 پنل ادمین")
async def admin_panel(message: Message):
    if not await _is_admin(message.from_user):
        return
    await message.answer("پنل ادمین:", reply_markup=admin_panel_keyboard())


@router.message(F.text == "/setup")
async def setup_command(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        return
    await state.set_state(SetupStates.wizard)
    await state.update_data(step_index=0)
    await message.answer("اوکی، راه‌اندازی مرحله‌ای شروع شد.")
    await _send_setup_step(message, 0)


@router.callback_query(F.data == "adm_setup")
async def setup_callback(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await state.set_state(SetupStates.wizard)
    await state.update_data(step_index=0)
    await callback.message.answer("اوکی، راه‌اندازی مرحله‌ای شروع شد.")
    await _send_setup_step(callback.message, 0)
    await callback.answer()


@router.message(SetupStates.wizard)
async def setup_wizard_answer(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    data = await state.get_data()
    index = int(data.get("step_index", 0))
    if index >= len(SETUP_STEPS):
        await state.clear()
        await message.answer("راه‌اندازی تمام شده.", reply_markup=main_menu(True))
        return
    await _advance_setup(message, state, index, message.text or "")


@router.callback_query(SetupStates.wizard, F.data.startswith("setup:"))
async def setup_button(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await state.clear()
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    index = int(data.get("step_index", 0))
    if index >= len(SETUP_STEPS):
        await state.clear()
        await callback.message.answer("راه‌اندازی تمام شده.", reply_markup=main_menu(True))
        await callback.answer()
        return
    step = SETUP_STEPS[index]
    if action == "noop":
        await callback.answer()
        return
    if action == "cancel":
        await state.clear()
        await callback.message.answer("راه‌اندازی لغو شد.", reply_markup=admin_panel_keyboard())
        await callback.answer()
        return
    if action == "back":
        new_index = max(index - 1, 0)
        await state.update_data(step_index=new_index)
        await _send_setup_step(callback.message, new_index)
        await callback.answer()
        return
    raw_value = {"skip": "skip", "default": str(step.get("default", "")), "same": "same", "empty": "-"}.get(action)
    if raw_value is None:
        await callback.answer("گزینه نامعتبر است.", show_alert=True)
        return
    await _advance_setup(callback.message, state, index, raw_value)
    await callback.answer()


@router.message(F.text == "/settings")
async def settings_command(message: Message):
    if not await _is_admin(message.from_user):
        return
    await message.answer(_settings_text(), reply_markup=settings_keyboard())


@router.callback_query(F.data == "adm_settings")
async def settings_callback(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await callback.message.answer(_settings_text(), reply_markup=settings_keyboard())
    await callback.answer()


@router.message(F.text.startswith("/set"))
async def set_config_command(message: Message):
    if not await _is_admin(message.from_user):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("فرمت: /set KEY value\nمثال: /set USD_RATE_TOMAN 150000")
        return
    key = parts[1].strip().upper()
    value = parts[2].strip()
    ok, msg = await _save_config_value(key, value)
    await message.answer(msg)


@router.callback_query(F.data == "adm_test_xui")
async def admin_test_xui(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    settings = get_settings()
    if not settings.xui_base_url or not settings.xui_api_key:
        await callback.message.answer("اول XUI_BASE_URL و XUI_API_KEY را با /setup تنظیم کن.")
        await callback.answer()
        return
    try:
        data = await XUIClient().list_inbounds()
    except XUIError as exc:
        await callback.message.answer(f"❌ اتصال 3x-ui ناموفق بود:\n<code>{html.escape(str(exc))}</code>")
    except Exception as exc:
        await callback.message.answer(f"❌ خطای غیرمنتظره در تست 3x-ui:\n<code>{html.escape(str(exc))}</code>")
    else:
        count = "نامشخص"
        if isinstance(data, dict):
            obj = data.get("obj") or data.get("data") or data.get("inbounds")
            if isinstance(obj, list):
                count = str(len(obj))
        await callback.message.answer(f"✅ اتصال 3x-ui موفق بود. تعداد inboundهای تشخیص‌داده‌شده: {count}")
    await callback.answer()


@router.callback_query(F.data == "adm_test_now")
async def admin_test_now(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    if not get_settings().nowpayments_api_key:
        await callback.message.answer("اول NOWPAYMENTS_API_KEY را با /setup تنظیم کن.")
        await callback.answer()
        return
    try:
        await NowPaymentsClient().check_api_key()
    except NowPaymentsError as exc:
        await callback.message.answer(f"❌ اتصال NOWPayments ناموفق بود:\n<code>{html.escape(str(exc))}</code>")
    except Exception as exc:
        await callback.message.answer(f"❌ خطای غیرمنتظره در تست NOWPayments:\n<code>{html.escape(str(exc))}</code>")
    else:
        await callback.message.answer("✅ API key درگاه NOWPayments جواب داد.")
    await callback.answer()


@router.callback_query(F.data == "adm_add_plan")
async def admin_add_plan_button(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await state.set_state(AddPlanStates.waiting_payload)
    await callback.message.answer("➕ اطلاعات پلن را بفرست:\n<code>20 گیگ یک‌ماهه|20|30|150000|1</code>\n\nفرمت: نام|حجم گیگ|مدت روز|قیمت تومان|inbound_id\nبرای لغو بنویس: cancel")
    await callback.answer()


@router.message(AddPlanStates.waiting_payload)
async def add_plan_payload(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("افزودن پلن لغو شد.", reply_markup=admin_panel_keyboard())
        return
    ok, msg = await _save_config_value("PLAN", text)
    if ok:
        await state.clear()
        await message.answer(msg, reply_markup=admin_panel_keyboard())
    else:
        await message.answer(msg + "\n\nدوباره با فرمت درست بفرست یا cancel کن.")


@router.callback_query(F.data == "adm_pending_receipts")
async def admin_pending_receipts(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    async with session_scope() as session:
        result = await session.execute(select(ManualReceipt).where(ManualReceipt.status == "pending").order_by(ManualReceipt.created_at.asc()).limit(20))
        receipts = result.scalars().all()
    if not receipts:
        await callback.message.answer("رسید در انتظاری نداریم.")
    else:
        lines = [f"#{r.id} | {fmt_toman(r.amount_toman)} | order={r.order_id or '-'}" for r in receipts]
        await callback.message.answer("🧾 رسیدهای در انتظار:\n" + "\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "adm_plans")
async def admin_plans(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    async with session_scope() as session:
        plans = (await session.execute(select(Plan).order_by(Plan.id))).scalars().all()
    if not plans:
        await callback.message.answer("پلنی تعریف نشده. با دکمه افزودن پلن یکی بساز.", reply_markup=admin_panel_keyboard())
    else:
        text = "\n".join(f"#{p.id} | {p.name} | {p.traffic_gb}GB/{p.days}d | {fmt_toman(p.price_toman)} | inbound={p.xui_inbound_id} | {'فعال' if p.active else 'غیرفعال'}" for p in plans)
        await callback.message.answer(text, reply_markup=plans_admin_keyboard(plans))
    await callback.answer()


@router.callback_query(F.data.startswith("adm_plan_view:"))
async def admin_plan_view(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    plan_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        plan = await session.get(Plan, plan_id)
    if not plan:
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return
    await callback.message.answer(f"📦 پلن #{plan.id}\nنام: {html.escape(plan.name)}\nحجم: {plan.traffic_gb} گیگ\nمدت: {plan.days} روز\nقیمت: {fmt_toman(plan.price_toman)}\ninbound: {plan.xui_inbound_id}\nlimit IP: {plan.xui_limit_ip}\nflow: {html.escape(plan.xui_flow or '-')}\nوضعیت: {'فعال' if plan.active else 'غیرفعال'}", reply_markup=plan_detail_keyboard(plan.id))
    await callback.answer()


@router.callback_query(F.data.startswith("adm_plan_toggle:"))
async def admin_plan_toggle(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    plan_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        plan = await session.get(Plan, plan_id)
        if not plan:
            await callback.answer("پلن پیدا نشد.", show_alert=True)
            return
        plan.active = not plan.active
        status = "فعال" if plan.active else "غیرفعال"
    await callback.message.answer(f"پلن #{plan_id} {status} شد.", reply_markup=admin_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("adm_receipt_ok:"))
async def admin_approve_receipt(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    receipt_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        receipt, result = await approve_manual_receipt(session, receipt_id=receipt_id, admin_telegram_id=callback.from_user.id)
        user = await session.get(User, receipt.user_id)
        service = result.service
        action = result.action
    try:
        await callback.message.edit_caption((callback.message.caption or "") + "\n\n✅ تایید شد")
    except Exception:
        pass
    if user:
        if service:
            title = "تمدید شد" if action == "renew" else "ساخته شد"
            await callback.bot.send_message(user.telegram_id, f"✅ رسید تایید شد و سرویس {title}.\n\nنام کلاینت: <code>{html.escape(service.email)}</code>\nلینک اشتراک:\n<code>{html.escape(service.link)}</code>")
        else:
            await callback.bot.send_message(user.telegram_id, f"✅ رسید تایید شد و {fmt_toman(receipt.amount_toman)} به کیف پولت اضافه شد.")
    await callback.answer("تایید شد")


@router.callback_query(F.data.startswith("adm_receipt_no:"))
async def admin_reject_receipt(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    receipt_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        receipt = await reject_manual_receipt(session, receipt_id=receipt_id, admin_telegram_id=callback.from_user.id)
        user = await session.get(User, receipt.user_id)
    try:
        await callback.message.edit_caption((callback.message.caption or "") + "\n\n❌ رد شد")
    except Exception:
        pass
    if user:
        await callback.bot.send_message(user.telegram_id, "❌ رسید کارت‌به‌کارت رد شد. لطفاً با پشتیبانی هماهنگ کن.")
    await callback.answer("رد شد")


@router.callback_query(F.data == "adm_report")
async def admin_report(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    async with session_scope() as session:
        users_count = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active_services = (await session.execute(select(func.count(Service.id)).where(Service.status == "active"))).scalar() or 0
        expired_services = (await session.execute(select(func.count(Service.id)).where(Service.status == "expired"))).scalar() or 0
        completed_orders = (await session.execute(select(func.count(Order.id)).where(Order.status == "completed"))).scalar() or 0
        sales_sum = (await session.execute(select(func.coalesce(func.sum(Order.amount_toman), 0)).where(Order.status == "completed", Order.order_type.in_(["buy", "renew"])))) .scalar() or 0
        wallet_topups = (await session.execute(select(func.coalesce(func.sum(Transaction.amount_toman), 0)).where(Transaction.amount_toman > 0))).scalar() or 0
        pending_receipts = (await session.execute(select(func.count(ManualReceipt.id)).where(ManualReceipt.status == "pending"))).scalar() or 0
    await callback.message.answer(
        "📊 گزارش فروش\n\n"
        f"کاربران: {users_count}\n"
        f"سرویس فعال: {active_services}\n"
        f"سرویس منقضی: {expired_services}\n"
        f"سفارش تکمیل‌شده: {completed_orders}\n"
        f"فروش خرید/تمدید: {fmt_toman(int(sales_sum))}\n"
        f"کل شارژهای مثبت کیف پول: {fmt_toman(int(wallet_topups))}\n"
        f"رسیدهای در انتظار: {pending_receipts}",
        reply_markup=admin_panel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm_users")
async def admin_users(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    async with session_scope() as session:
        users = (await session.execute(select(User).order_by(User.created_at.desc()).limit(20))).scalars().all()
    if not users:
        await callback.message.answer("کاربری نداریم.", reply_markup=admin_back_keyboard())
    else:
        await callback.message.answer("👥 مدیریت کاربران\nیکی را انتخاب کن یا جستجو بزن:", reply_markup=users_admin_keyboard(users))
    await callback.answer()



@router.callback_query(F.data.startswith("adm_user:"))
async def admin_user_detail(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    await _send_user_detail(callback.message, tg_id)
    await callback.answer()


@router.callback_query(F.data == "adm_user_search")
async def admin_user_search(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await state.set_state(UserAdminStates.waiting_search)
    await callback.message.answer("🔎 آیدی عددی تلگرام یا username کاربر را بفرست. برای لغو: cancel", reply_markup=admin_back_keyboard("adm_users"))
    await callback.answer()


@router.message(UserAdminStates.waiting_search)
async def admin_user_search_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    query = (message.text or "").strip().lstrip("@")
    if query.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=admin_back_keyboard("adm_users"))
        return
    async with session_scope() as session:
        if query.isdigit():
            user = await get_user_by_telegram_id(session, int(query))
        else:
            user = (await session.execute(select(User).where(User.username == query))).scalars().first()
    await state.clear()
    if not user:
        await message.answer("کاربر پیدا نشد.", reply_markup=admin_back_keyboard("adm_users"))
        return
    await _send_user_detail(message, user.telegram_id)


@router.callback_query(F.data.startswith("adm_user_toggle_admin:"))
async def admin_user_toggle_admin(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    if tg_id == callback.from_user.id:
        await callback.answer("نقش ادمین خودت را از اینجا تغییر نده.", show_alert=True)
        return
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("کاربر پیدا نشد.", show_alert=True)
            return
        user.is_admin = not user.is_admin
    await callback.message.answer("✅ نقش ادمین تغییر کرد.")
    await _send_user_detail(callback.message, tg_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_user_toggle_reseller:"))
async def admin_user_toggle_reseller(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("کاربر پیدا نشد.", show_alert=True)
            return
        user.is_reseller = not user.is_reseller
    await callback.message.answer("✅ نقش نماینده تغییر کرد.")
    await _send_user_detail(callback.message, tg_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_user_balance:"))
async def admin_user_balance_start(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    _, tg_id, mode = callback.data.replace("adm_user_balance:", "").split(":", 2)
    await state.set_state(UserAdminStates.waiting_balance)
    await state.update_data(balance_tg_id=int(tg_id), balance_mode=mode)
    sign = "افزایش" if mode == "plus" else "کاهش"
    await callback.message.answer(f"مبلغ {sign} موجودی را به تومان بفرست. مثال: 50000\nبرای لغو: cancel", reply_markup=admin_back_keyboard(f"adm_user:{tg_id}"))
    await callback.answer()


@router.message(UserAdminStates.waiting_balance)
async def admin_user_balance_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = (message.text or "").strip().replace(",", "")
    if text.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=admin_back_keyboard("adm_users"))
        return
    if not text.isdigit():
        await message.answer("فقط عدد تومان بفرست.")
        return
    data = await state.get_data()
    tg_id = int(data.get("balance_tg_id"))
    mode = data.get("balance_mode")
    amount = int(text) * (1 if mode == "plus" else -1)
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer("کاربر پیدا نشد.")
            await state.clear()
            return
        user.balance_toman += amount
        session.add(Transaction(user_id=user.id, amount_toman=amount, tx_type="admin_adjust", ref_type="admin", description=f"Admin adjust by {message.from_user.id}"))
    await state.clear()
    await message.answer("✅ موجودی تغییر کرد.")
    await _send_user_detail(message, tg_id)


@router.callback_query(F.data.startswith("adm_user_services:"))
async def admin_user_services(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    tg_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            await callback.answer("کاربر پیدا نشد.", show_alert=True)
            return
        services = (await session.execute(select(Service).where(Service.user_id == user.id).order_by(Service.created_at.desc()).limit(20))).scalars().all()
    if not services:
        await callback.message.answer("این کاربر سرویسی ندارد.", reply_markup=admin_back_keyboard(f"adm_user:{tg_id}"))
    else:
        lines = [f"#{s.id} | {s.email} | {s.status} | inbound={s.xui_inbound_id}" for s in services]
        await callback.message.answer("📦 سرویس‌های کاربر:\n" + "\n".join(lines), reply_markup=admin_back_keyboard(f"adm_user:{tg_id}"))
    await callback.answer()


@router.callback_query(F.data.startswith("adm_plan_edit:"))
async def admin_plan_edit_start(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    _, plan_id, field = callback.data.replace("adm_plan_edit:", "").split(":", 2)
    allowed = {"name", "traffic_gb", "days", "price_toman", "xui_inbound_id"}
    if field not in allowed:
        await callback.answer("فیلد نامعتبر است.", show_alert=True)
        return
    await state.set_state(EditPlanStates.waiting_value)
    await state.update_data(plan_id=int(plan_id), plan_field=field)
    await callback.message.answer(f"مقدار جدید برای <code>{html.escape(field)}</code> را بفرست.\nبرای لغو: cancel", reply_markup=admin_back_keyboard(f"adm_plan_view:{plan_id}"))
    await callback.answer()


@router.message(EditPlanStates.waiting_value)
async def admin_plan_edit_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = (message.text or "").strip().replace(",", "")
    if text.lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=admin_back_keyboard("adm_plans"))
        return
    data = await state.get_data()
    plan_id = int(data.get("plan_id"))
    field = str(data.get("plan_field"))
    numeric_fields = {"traffic_gb", "days", "price_toman", "xui_inbound_id"}
    if field in numeric_fields and not text.isdigit():
        await message.answer("برای این فیلد فقط عدد بفرست.")
        return
    async with session_scope() as session:
        plan = await session.get(Plan, plan_id)
        if not plan:
            await message.answer("پلن پیدا نشد.")
            await state.clear()
            return
        setattr(plan, field, int(text) if field in numeric_fields else text)
    await state.clear()
    await message.answer("✅ پلن ویرایش شد.")
    # Show updated plan using a small duplicated view to avoid fabricating callback objects.
    async with session_scope() as session:
        plan = await session.get(Plan, plan_id)
    if plan:
        await message.answer(f"📦 پلن #{plan.id}\nنام: {html.escape(plan.name)}\nحجم: {plan.traffic_gb} گیگ\nمدت: {plan.days} روز\nقیمت: {fmt_toman(plan.price_toman)}\ninbound: {plan.xui_inbound_id}\nوضعیت: {'فعال' if plan.active else 'غیرفعال'}", reply_markup=plan_detail_keyboard(plan.id))


@router.callback_query(F.data == "adm_texts")
async def admin_texts(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await callback.message.answer("📝 کدام متن را تغییر می‌دهی؟", reply_markup=texts_keyboard(list(TEXTS.keys())))
    await callback.answer()


@router.callback_query(F.data.startswith("adm_text_edit:"))
async def admin_text_edit(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    async with session_scope() as session:
        current = await get_text(session, key)
    await state.set_state(EditTextStates.waiting_text)
    await state.update_data(text_key=key)
    await callback.message.answer(f"متن فعلی <b>{html.escape(key)}</b>:\n\n{html.escape(current)}\n\nمتن جدید را بفرست. برای لغو بنویس cancel")
    await callback.answer()


@router.message(EditTextStates.waiting_text)
async def admin_text_save(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    text = message.text or ""
    if text.strip().lower() in {"cancel", "لغو"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=admin_panel_keyboard())
        return
    data = await state.get_data()
    key = data.get("text_key")
    async with session_scope() as session:
        await set_text(session, key, text)
    await state.clear()
    await message.answer(f"متن {key} ذخیره شد.", reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "adm_backup")
async def admin_backup(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    path = Path("shop.db")
    settings = get_settings()
    if settings.database_url.startswith("sqlite+aiosqlite:///"):
        path = Path(settings.database_url.replace("sqlite+aiosqlite:///", "", 1))
    if not path.exists():
        await callback.message.answer("فایل دیتابیس پیدا نشد.")
    else:
        await callback.message.answer_document(FSInputFile(path), caption="📤 بکاپ دیتابیس SQLite")
    await callback.answer()


@router.message(F.text.startswith("/addadmin"))
async def add_admin(message: Message):
    if not await _is_admin(message.from_user):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت: /addadmin 123456789")
        return
    tg_id = int(parts[1])
    async with session_scope() as session:
        user = await get_user_by_telegram_id(session, tg_id)
        if not user:
            user = User(telegram_id=tg_id, is_admin=True)
            session.add(user)
        else:
            user.is_admin = True
    await message.answer(f"ادمین اضافه شد: {tg_id}")


@router.message(F.text.startswith("/addplan"))
async def add_plan(message: Message):
    if not await _is_admin(message.from_user):
        return
    payload = (message.text or "").replace("/addplan", "", 1).strip()
    ok, msg = await _save_config_value("PLAN", payload)
    await message.answer(msg, reply_markup=main_menu(True))
