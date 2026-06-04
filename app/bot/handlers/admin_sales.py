from __future__ import annotations

import html
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.keyboards import admin_panel_keyboard, back_to_menu_keyboard, pay_methods_keyboard, plans_keyboard
from app.config import reload_settings
from app.db import session_scope
from app.models import Order, Plan, Service, XuiInbound
from app.panels.xui import XUIClient
from app.pricing import fmt_toman
from app.runtime_config import update_runtime_config
from app.services.accounts import get_or_create_user

router = Router()


class PlanWizardStates(StatesGroup):
    waiting_name = State()
    waiting_traffic = State()
    waiting_days = State()
    waiting_price = State()
    waiting_inbound = State()


def _is_cancel(text: str | None) -> bool:
    return (text or "").strip().lower() in {"cancel", "لغو"}


def _normalize_int(text: str | None) -> int | None:
    raw = (text or "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


async def _is_admin(telegram_user) -> bool:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        return bool(user.is_admin)


async def _active_inbounds(session) -> list[XuiInbound]:
    result = await session.execute(
        select(XuiInbound)
        .where(XuiInbound.active == True)  # noqa: E712
        .order_by(XuiInbound.is_default.desc(), XuiInbound.inbound_id.asc())
    )
    return list(result.scalars().all())


async def _inbound_for_plan(session, plan: Plan) -> XuiInbound | None:
    result = await session.execute(
        select(XuiInbound).where(
            XuiInbound.inbound_id == plan.xui_inbound_id,
            XuiInbound.active == True,  # noqa: E712
        )
    )
    return result.scalars().first()


def _plan_inbound_picker(inbounds: list[XuiInbound]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for inbound in inbounds:
        default = "⭐ " if inbound.is_default else ""
        flow = f" | flow={inbound.flow}" if inbound.flow else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{default}{inbound.title} | inbound={inbound.inbound_id} | limitIp={inbound.limit_ip}{flow}",
                    callback_data=f"planwiz_inbound:{inbound.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🌐 مدیریت/لود اینباندها", callback_data="adm_inbounds")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _client_inbounds_keyboard(inbounds: list[XuiInbound]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for inbound in inbounds:
        status = "✅ فعال" if inbound.active else "⛔ غیرفعال"
        default = "⭐ " if inbound.is_default else ""
        title = inbound.title or f"Inbound {inbound.inbound_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} | {default}{title} | id={inbound.inbound_id}",
                    callback_data=f"client_inbound_toggle:{inbound.id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="⭐ پیش‌فرض فروش", callback_data=f"client_inbound_default:{inbound.id}"),
                InlineKeyboardButton(text="✏️ جزئیات قدیمی", callback_data=f"adm_inbound:{inbound.id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="📥 لود همه اینباندها از 3x-ui", callback_data="adm_pull_inbounds")])
    rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_client_inbounds(message: Message) -> None:
    async with session_scope() as session:
        inbounds = (
            await session.execute(
                select(XuiInbound).order_by(
                    XuiInbound.active.desc(),
                    XuiInbound.is_default.desc(),
                    XuiInbound.inbound_id.asc(),
                )
            )
        ).scalars().all()
    text = (
        "🌐 مدیریت گروه/اینباند کلاینت‌ها\n\n"
        "هر کلاینتی که ساخته می‌شود باید به یکی از این inboundها وصل باشد. "
        "فقط inboundهای ✅ فعال برای ساخت/تمدید سرویس و نمایش پلن قابل استفاده‌اند.\n\n"
        "روی هر inbound بزن تا برای کلاینت‌ها فعال/غیرفعال شود."
    )
    if not inbounds:
        text += "\n\nهنوز inboundی لود نشده؛ دکمه لود از 3x-ui را بزن."
    await message.answer(text, reply_markup=_client_inbounds_keyboard(list(inbounds)))


async def _send_sales_plans(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        active_ids = {item.inbound_id for item in await _active_inbounds(session)}
        result = await session.execute(select(Plan).where(Plan.active == True).order_by(Plan.price_toman))  # noqa: E712
        plans = [plan for plan in result.scalars().all() if plan.xui_inbound_id in active_ids]
        is_admin = user.is_admin
    if not plans:
        await target.answer(
            "فعلاً پلن قابل فروش نداریم. اول از پنل مدیریت، اینباندها را لود و فعال کن و بعد پلن بساز.",
            reply_markup=back_to_menu_keyboard(is_admin),
        )
        return
    await target.answer("🛒 یکی از پلن‌های فعال را انتخاب کن:", reply_markup=plans_keyboard(plans))


async def _start_plan_wizard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PlanWizardStates.waiting_name)
    await message.answer(
        "➕ افزودن پلن جدید\n\n"
        "دیگه لازم نیست فرمت خطی مثل name|gb|days|price بزنی. مرحله‌به‌مرحله می‌پرسم.\n\n"
        "اول اسم پلن را بفرست. مثال: 20 گیگ یک‌ماهه\nبرای لغو: cancel",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data == "adm_add_plan")
async def add_plan_wizard_button(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await _start_plan_wizard(callback.message, state)
    await callback.answer()


@router.message(F.text.startswith("/addplan"))
async def add_plan_wizard_command(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        return
    await _start_plan_wizard(message, state)


@router.message(PlanWizardStates.waiting_name)
async def plan_wizard_name(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("افزودن پلن لغو شد.", reply_markup=admin_panel_keyboard())
        return
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 160:
        await message.answer("اسم پلن باید بین ۲ تا ۱۶۰ کاراکتر باشد.")
        return
    await state.update_data(name=name)
    await state.set_state(PlanWizardStates.waiting_traffic)
    await message.answer("حجم پلن را به گیگابایت بفرست. مثال: 20")


@router.message(PlanWizardStates.waiting_traffic)
async def plan_wizard_traffic(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("افزودن پلن لغو شد.", reply_markup=admin_panel_keyboard())
        return
    traffic_gb = _normalize_int(message.text)
    if traffic_gb is None or not 1 <= traffic_gb <= 100_000:
        await message.answer("حجم باید عددی بین ۱ تا ۱۰۰۰۰۰ گیگ باشد. مثال: 20")
        return
    await state.update_data(traffic_gb=traffic_gb)
    await state.set_state(PlanWizardStates.waiting_days)
    await message.answer("مدت پلن را به روز بفرست. مثال: 30")


@router.message(PlanWizardStates.waiting_days)
async def plan_wizard_days(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("افزودن پلن لغو شد.", reply_markup=admin_panel_keyboard())
        return
    days = _normalize_int(message.text)
    if days is None or not 1 <= days <= 3650:
        await message.answer("مدت باید عددی بین ۱ تا ۳۶۵۰ روز باشد. مثال: 30")
        return
    await state.update_data(days=days)
    await state.set_state(PlanWizardStates.waiting_price)
    await message.answer("قیمت پلن را به تومان بفرست. مثال: 150000")


@router.message(PlanWizardStates.waiting_price)
async def plan_wizard_price(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user):
        await state.clear()
        return
    if _is_cancel(message.text):
        await state.clear()
        await message.answer("افزودن پلن لغو شد.", reply_markup=admin_panel_keyboard())
        return
    price_toman = _normalize_int(message.text)
    if price_toman is None or not 1_000 <= price_toman <= 10_000_000_000:
        await message.answer("قیمت باید عدد تومان معتبر باشد. مثال: 150000")
        return
    await state.update_data(price_toman=price_toman)
    async with session_scope() as session:
        inbounds = await _active_inbounds(session)
    if not inbounds:
        await state.clear()
        await message.answer(
            "هیچ inbound فعالی برای ساخت کلاینت وجود ندارد. اول از بخش اینباندها، لیست را از 3x-ui لود و حداقل یکی را فعال کن؛ بعد دوباره افزودن پلن را بزن.",
            reply_markup=_client_inbounds_keyboard([]),
        )
        return
    await state.set_state(PlanWizardStates.waiting_inbound)
    await message.answer("حالا گروه/اینباندی که کلاینت‌های این پلن داخلش ساخته شوند را انتخاب کن:", reply_markup=_plan_inbound_picker(inbounds))


@router.callback_query(PlanWizardStates.waiting_inbound, F.data.startswith("planwiz_inbound:"))
async def plan_wizard_inbound(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user):
        await state.clear()
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    inbound_db_id = int(callback.data.split(":", 1)[1])
    data: dict[str, Any] = await state.get_data()
    async with session_scope() as session:
        inbound = await session.get(XuiInbound, inbound_db_id)
        if not inbound or not inbound.active:
            await callback.message.answer("این inbound فعال نیست یا پیدا نشد. یکی از inboundهای فعال را انتخاب کن.")
            await callback.answer()
            return
        plan = Plan(
            name=str(data["name"]),
            traffic_gb=int(data["traffic_gb"]),
            days=int(data["days"]),
            price_toman=int(data["price_toman"]),
            xui_inbound_id=inbound.inbound_id,
            xui_limit_ip=inbound.limit_ip,
            xui_flow=inbound.flow or "",
            active=True,
        )
        session.add(plan)
        await session.flush()
        plan_id = plan.id
    await state.clear()
    await callback.message.answer(
        "✅ پلن ساخته شد.\n\n"
        f"پلن #{plan_id}\n"
        f"نام: <b>{html.escape(str(data['name']))}</b>\n"
        f"حجم: {int(data['traffic_gb'])} گیگ\n"
        f"مدت: {int(data['days'])} روز\n"
        f"قیمت: {fmt_toman(int(data['price_toman']))}\n"
        f"گروه/اینباند کلاینت‌ها: <code>{inbound.inbound_id}</code> - {html.escape(inbound.title)}",
        reply_markup=admin_panel_keyboard(),
    )
    await callback.answer("پلن اضافه شد")


@router.callback_query(F.data == "adm_inbounds")
async def enhanced_admin_inbounds(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    await _send_client_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data == "adm_pull_inbounds")
async def enhanced_pull_inbounds(callback: CallbackQuery):
    if not await _is_admin(callback.from_user):
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    try:
        data = await XUIClient().list_inbounds()
    except Exception as exc:
        await callback.message.answer(f"❌ دریافت اینباند از 3x-ui ناموفق بود:\n<code>{html.escape(str(exc))}</code>")
        await callback.answer()
        return

    raw = []
    if isinstance(data, dict):
        obj = data.get("obj") or data.get("data") or data.get("inbounds")
        if isinstance(obj, list):
            raw = obj

    added = 0
    updated = 0
    async with session_scope() as session:
        existing = {
            inbound.inbound_id: inbound
            for inbound in (await session.execute(select(XuiInbound))).scalars().all()
        }
        has_default = any(item.is_default for item in existing.values())
        for item in raw:
            if not isinstance(item, dict):
                continue
            inbound_num = item.get("id") or item.get("inbound_id")
            if not isinstance(inbound_num, int):
                continue
            title = str(item.get("remark") or item.get("tag") or item.get("protocol") or f"Inbound {inbound_num}")
            inbound = existing.get(inbound_num)
            if inbound:
                if inbound.title != title:
                    inbound.title = title
                    updated += 1
                continue
            inbound = XuiInbound(
                title=title,
                inbound_id=inbound_num,
                active=True,
                is_default=not has_default and added == 0,
            )
            session.add(inbound)
            existing[inbound_num] = inbound
            added += 1
            if inbound.is_default:
                has_default = True
                update_runtime_config({
                    "XUI_DEFAULT_INBOUND_ID": inbound_num,
                    "XUI_DEFAULT_LIMIT_IP": inbound.limit_ip,
                    "XUI_DEFAULT_FLOW": inbound.flow or "",
                })
                reload_settings()
    await callback.message.answer(f"✅ لود اینباندها تمام شد. {added} مورد جدید اضافه شد، {updated} مورد بروزرسانی شد.")
    await _send_client_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("client_inbound_toggle:") | F.data.startswith("adm_inbound_toggle:"))
async def toggle_client_inbound(callback: CallbackQuery):
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
        if not inbound.active and inbound.is_default:
            inbound.is_default = False
        status = "فعال" if inbound.active else "غیرفعال"
    await callback.message.answer(f"✅ اینباند برای ساخت کلاینت {status} شد.")
    await _send_client_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("client_inbound_default:"))
async def set_default_client_inbound(callback: CallbackQuery):
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
        inbound.active = True
        update_runtime_config({
            "XUI_DEFAULT_INBOUND_ID": inbound.inbound_id,
            "XUI_DEFAULT_LIMIT_IP": inbound.limit_ip,
            "XUI_DEFAULT_FLOW": inbound.flow or "",
        })
        reload_settings()
    await callback.message.answer("✅ اینباند پیش‌فرض فروش شد و برای ساخت کلاینت فعال است.")
    await _send_client_inbounds(callback.message)
    await callback.answer()


@router.callback_query(F.data == "menu:plans")
async def menu_plans_active_inbounds(callback: CallbackQuery):
    await _send_sales_plans(callback.message, callback.from_user)
    await callback.answer()


@router.message(F.text == "🛒 خرید سرویس")
async def plans_active_inbounds(message: Message):
    await _send_sales_plans(message, message.from_user)


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan_with_active_inbound(callback: CallbackQuery):
    plan_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        plan = await session.get(Plan, plan_id)
        if not plan or not plan.active:
            await callback.answer("این پلن فعال نیست.", show_alert=True)
            return
        inbound = await _inbound_for_plan(session, plan)
        if not inbound:
            await callback.answer("اینباند این پلن برای ساخت کلاینت فعال نیست.", show_alert=True)
            return
        order = Order(
            user_id=user.id,
            plan_id=plan.id,
            order_type="buy",
            amount_toman=plan.price_toman,
            status="pending",
            payment_method="select",
        )
        session.add(order)
        await session.flush()
        text = (
            "🛒 سفارش خرید\n\n"
            f"پلن: <b>{html.escape(plan.name)}</b>\n"
            f"گروه/اینباند: <code>{inbound.inbound_id}</code> - {html.escape(inbound.title)}\n"
            f"حجم: {plan.traffic_gb} گیگ\n"
            f"مدت: {plan.days} روز\n"
            f"قیمت: {fmt_toman(plan.price_toman)}\n"
            f"موجودی شما: {fmt_toman(user.balance_toman)}"
        )
        await callback.message.answer(text, reply_markup=pay_methods_keyboard(order.id))
    await callback.answer()


@router.callback_query(F.data.startswith("renew_same:"))
async def renew_same_with_active_inbound(callback: CallbackQuery):
    service_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        service = await session.get(Service, service_id)
        if not service or service.user_id != user.id:
            await callback.answer("سرویس پیدا نشد.", show_alert=True)
            return
        plan = await session.get(Plan, service.plan_id)
        if not plan or not plan.active:
            await callback.answer("پلن این سرویس فعال نیست؛ از ادمین کمک بگیر.", show_alert=True)
            return
        inbound = await _inbound_for_plan(session, plan)
        if not inbound:
            await callback.answer("اینباند این پلن فعلاً برای تمدید فعال نیست.", show_alert=True)
            return
        order = Order(
            user_id=user.id,
            plan_id=plan.id,
            service_id=service.id,
            order_type="renew",
            amount_toman=plan.price_toman,
            status="pending",
            payment_method="select",
        )
        session.add(order)
        await session.flush()
        await callback.message.answer(
            f"🔁 تمدید سرویس #{service.id}\n"
            f"پلن: <b>{html.escape(plan.name)}</b>\n"
            f"گروه/اینباند: <code>{inbound.inbound_id}</code> - {html.escape(inbound.title)}\n"
            f"مدت تمدید: {plan.days} روز\n"
            f"مبلغ: {fmt_toman(plan.price_toman)}",
            reply_markup=pay_methods_keyboard(order.id),
        )
    await callback.answer()
