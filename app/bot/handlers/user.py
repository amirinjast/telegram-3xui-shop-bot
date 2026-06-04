from __future__ import annotations

import html
import json
from datetime import timezone
from io import BytesIO

import qrcode
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import (
    back_to_menu_keyboard,
    help_keyboard,
    main_menu,
    pay_methods_keyboard,
    plans_keyboard,
    service_detail_keyboard,
    services_keyboard,
    topup_keyboard,
    topup_method_keyboard,
)
from app.config import get_settings
from app.db import session_scope
from app.messages import get_text
from app.models import ManualReceipt, Order, Payment, Plan, Service
from app.payments.nowpayments import NowPaymentsClient, NowPaymentsError
from app.pricing import fmt_toman, toman_to_usd
from app.services.accounts import get_admin_telegram_ids, get_or_create_user
from app.services.billing import complete_paid_order, debit_wallet

router = Router()


class TopupStates(StatesGroup):
    custom_amount = State()
    waiting_receipt = State()


def _date(value) -> str:
    if not value:
        return "-"
    try:
        if value.tzinfo:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value)


def _service_text(service: Service) -> str:
    return (
        f"📦 سرویس #{service.id}\n"
        f"وضعیت: <b>{html.escape(service.status)}</b>\n"
        f"نام کلاینت: <code>{html.escape(service.email)}</code>\n"
        f"حجم: {service.traffic_gb} گیگ\n"
        f"انقضا: <code>{_date(service.expires_at)}</code>"
    )


async def _send_home(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
    await target.answer("🏠 منوی اصلی\nیکی از گزینه‌ها رو بزن:", reply_markup=main_menu(user.is_admin))


async def _send_account(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        is_admin = user.is_admin
        balance = user.balance_toman
    await target.answer(f"👤 حساب من\n\nموجودی کیف پول: {fmt_toman(balance)}", reply_markup=back_to_menu_keyboard(is_admin))


async def _send_plans(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        result = await session.execute(select(Plan).where(Plan.active == True).order_by(Plan.price_toman))  # noqa: E712
        all_plans = result.scalars().all()
        is_admin = user.is_admin
    if not all_plans:
        await target.answer("فعلاً پلنی تعریف نشده.", reply_markup=back_to_menu_keyboard(is_admin))
        return
    await target.answer("🛒 یکی از پلن‌ها رو انتخاب کن:", reply_markup=plans_keyboard(all_plans))


async def _send_topup(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        await get_or_create_user(session, telegram_user)
    await target.answer("💳 مبلغ شارژ رو انتخاب کن:", reply_markup=topup_keyboard())


async def _send_services(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        is_admin = user.is_admin
        result = await session.execute(
            select(Service).where(Service.user_id == user.id).order_by(Service.created_at.desc()).limit(25)
        )
        services = result.scalars().all()
    if not services:
        await target.answer("هنوز سرویسی نداری.", reply_markup=back_to_menu_keyboard(is_admin))
        return
    await target.answer("📦 سرویس‌هات رو انتخاب کن:", reply_markup=services_keyboard(services))


async def _send_support(target: Message, telegram_user) -> None:
    async with session_scope() as session:
        user = await get_or_create_user(session, telegram_user)
        text = await get_text(session, "SUPPORT")
    await target.answer(text, reply_markup=back_to_menu_keyboard(user.is_admin))


async def _send_helps(target: Message) -> None:
    await target.answer("📚 آموزش اتصال برای کدوم دستگاه؟", reply_markup=help_keyboard())


@router.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    async with session_scope() as session:
        user = await get_or_create_user(session, message.from_user)
        welcome = await get_text(session, "WELCOME")
    settings = get_settings()
    await message.answer(welcome, reply_markup=main_menu(user.is_admin))

    if user.is_admin and (not settings.xui_base_url or not settings.xui_api_key):
        from app.bot.keyboards import settings_hub_keyboard

        await state.clear()
        await message.answer(
            "✅ تو ادمین اصلی هستی. تنظیمات هنوز کامل نیست، ولی دیگر مجبور نیستی setup مرحله‌ای را از اول بروی.\n"
            "از بخش‌های زیر فقط همان موردی را که می‌خواهی تنظیم کن.",
            reply_markup=settings_hub_keyboard(),
        )


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_home(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery):
    await _send_account(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:plans")
async def menu_plans(callback: CallbackQuery):
    await _send_plans(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:topup")
async def menu_topup(callback: CallbackQuery):
    await _send_topup(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:services")
async def menu_services(callback: CallbackQuery):
    await _send_services(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery):
    await _send_support(callback.message, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "menu:helps")
async def menu_helps(callback: CallbackQuery):
    await _send_helps(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("help:"))
async def help_item(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        text = await get_text(session, key)
    await callback.message.answer(text, reply_markup=back_to_menu_keyboard(user.is_admin))
    await callback.answer()


@router.callback_query(F.data == "menu:admin")
async def menu_admin(callback: CallbackQuery):
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
    if not user.is_admin:
        await callback.answer("دسترسی نداری.", show_alert=True)
        return
    from app.bot.keyboards import admin_panel_keyboard

    await callback.message.answer("🛠 پنل ادمین:", reply_markup=admin_panel_keyboard())
    await callback.answer()


@router.message(F.text == "👤 حساب من")
async def account(message: Message):
    await _send_account(message, message.from_user)


@router.message(F.text == "🛒 خرید سرویس")
async def plans(message: Message):
    await _send_plans(message, message.from_user)


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery):
    plan_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        plan = await session.get(Plan, plan_id)
        if not plan or not plan.active:
            await callback.answer("این پلن فعال نیست.", show_alert=True)
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
            f"🛒 سفارش خرید\n\n"
            f"پلن: <b>{html.escape(plan.name)}</b>\n"
            f"حجم: {plan.traffic_gb} گیگ\n"
            f"مدت: {plan.days} روز\n"
            f"قیمت: {fmt_toman(plan.price_toman)}\n"
            f"موجودی شما: {fmt_toman(user.balance_toman)}"
        )
        await callback.message.answer(text, reply_markup=pay_methods_keyboard(order.id))
    await callback.answer()


@router.callback_query(F.data.startswith("renew_same:"))
async def renew_same(callback: CallbackQuery):
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
            f"مدت تمدید: {plan.days} روز\n"
            f"مبلغ: {fmt_toman(plan.price_toman)}",
            reply_markup=pay_methods_keyboard(order.id),
        )
    await callback.answer()


async def _send_service_ready(message: Message, service: Service, is_admin: bool, *, renewed: bool = False) -> None:
    async with session_scope() as session:
        text_key = "RENEW_SUCCESS" if renewed else "BUY_SUCCESS"
        title = await get_text(session, text_key)
    await message.answer(
        f"{title}\n\n"
        f"نام کلاینت: <code>{html.escape(service.email)}</code>\n"
        f"انقضا: <code>{_date(service.expires_at)}</code>\n\n"
        f"لینک اشتراک:\n<code>{html.escape(service.link)}</code>",
        reply_markup=service_detail_keyboard(service.id, is_admin),
    )


@router.callback_query(F.data.startswith("pay_wallet:"))
async def pay_wallet(callback: CallbackQuery):
    order_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        order = await session.get(Order, order_id)
        if not order or order.user_id != user.id:
            await callback.answer("سفارش پیدا نشد.", show_alert=True)
            return
        if order.status != "pending":
            await callback.answer("این سفارش قبلاً پردازش شده.", show_alert=True)
            return
        if user.balance_toman < order.amount_toman:
            await callback.message.answer("موجودی کیف پول کافی نیست. اول حسابت رو شارژ کن.", reply_markup=topup_keyboard())
            await callback.answer()
            return
        await debit_wallet(
            session,
            user=user,
            amount_toman=order.amount_toman,
            tx_type=order.order_type,
            ref_type="order",
            ref_id=order.id,
            description=f"پرداخت سفارش #{order.id}",
        )
        result = await complete_paid_order(session, order=order)
        service = result.service
        is_admin = user.is_admin
        action = result.action
    if service:
        await _send_service_ready(callback.message, service, is_admin, renewed=(action == "renew"))
    else:
        await callback.message.answer("✅ پرداخت انجام شد.", reply_markup=back_to_menu_keyboard(is_admin))
    await callback.answer()


@router.callback_query(F.data.startswith("pay_now:"))
async def pay_nowpayments_invoice(callback: CallbackQuery):
    order_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        order = await session.get(Order, order_id)
        if not order or order.user_id != user.id:
            await callback.answer("سفارش پیدا نشد.", show_alert=True)
            return
        if order.status != "pending":
            await callback.answer("این سفارش قبلاً پردازش شده.", show_alert=True)
            return
        usd = toman_to_usd(order.amount_toman)
        np = NowPaymentsClient()
        try:
            invoice = await np.create_invoice(
                order_id=f"{order.order_type}-{order.id}",
                description=f"Telegram bot order {order.order_type}-{order.id}",
                amount_usd=usd,
            )
        except NowPaymentsError as exc:
            await callback.message.answer(f"خطا در ساخت لینک پرداخت: {html.escape(str(exc))}", reply_markup=back_to_menu_keyboard(user.is_admin))
            await callback.answer()
            return
        order.payment_method = "nowpayments"
        order.external_id = str(invoice.get("id") or invoice.get("invoice_id") or "")
        order.payload_json = json.dumps(invoice, ensure_ascii=False)
        payment = Payment(
            user_id=user.id,
            order_id=order.id,
            provider="nowpayments",
            provider_invoice_id=order.external_id,
            amount_toman=order.amount_toman,
            amount_usd=str(usd),
            status="pending",
            payload_json=json.dumps(invoice, ensure_ascii=False),
        )
        session.add(payment)
        url = invoice.get("invoice_url") or invoice.get("url") or invoice.get("payment_url")
        await callback.message.answer(
            f"مبلغ: {fmt_toman(order.amount_toman)}\n"
            f"معادل با نرخ ثابت: {usd} USD\n\n"
            f"بعد از تایید پرداخت، سرویس خودکار ساخته/تمدید می‌شود.\n\n"
            f"لینک پرداخت:\n{url or 'در پاسخ NOWPayments لینک مشخصی پیدا نشد؛ payload ذخیره شد.'}",
            reply_markup=back_to_menu_keyboard(user.is_admin),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_manual:"))
async def pay_manual_order(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":", 1)[1])
    settings = get_settings()
    if not settings.manual_payment_enabled or not settings.card_to_card_number:
        await callback.message.answer("پرداخت کارت‌به‌کارت فعلاً تنظیم/فعال نیست.")
        await callback.answer()
        return
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        order = await session.get(Order, order_id)
        if not order or order.user_id != user.id:
            await callback.answer("سفارش پیدا نشد.", show_alert=True)
            return
        await state.update_data(manual_order_id=order.id, manual_amount=order.amount_toman)
        await state.set_state(TopupStates.waiting_receipt)
        await callback.message.answer(
            f"برای پرداخت {fmt_toman(order.amount_toman)} به کارت زیر واریز کن و عکس رسید را همینجا بفرست:\n\n"
            f"کارت: <code>{html.escape(settings.card_to_card_number)}</code>\n"
            f"به نام: {html.escape(settings.card_to_card_owner or '-')}",
            reply_markup=back_to_menu_keyboard(user.is_admin),
        )
    await callback.answer()


@router.message(F.text == "💳 شارژ حساب")
async def topup(message: Message):
    await _send_topup(message, message.from_user)


@router.callback_query(F.data == "topup_custom")
async def topup_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TopupStates.custom_amount)
    await callback.message.answer("مبلغ را به تومان بفرست. مثال: 150000", reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.message(TopupStates.custom_amount)
async def topup_custom_amount(message: Message, state: FSMContext):
    settings = get_settings()
    raw = (message.text or "").replace(",", "").strip()
    if raw.lower() in {"cancel", "لغو"}:
        await state.clear()
        await _send_home(message, message.from_user)
        return
    if not raw.isdigit():
        await message.answer("فقط عدد بفرست. مثال: 150000")
        return
    amount = int(raw)
    if amount < settings.min_topup_toman:
        await message.answer(f"حداقل شارژ {fmt_toman(settings.min_topup_toman)} است.")
        return
    await state.clear()
    await message.answer(f"روش پرداخت برای {fmt_toman(amount)}:", reply_markup=topup_method_keyboard(amount))


@router.callback_query(F.data.startswith("topup_amount:"))
async def topup_amount(callback: CallbackQuery):
    amount = int(callback.data.split(":", 1)[1])
    await callback.message.answer(f"روش پرداخت برای {fmt_toman(amount)}:", reply_markup=topup_method_keyboard(amount))
    await callback.answer()


@router.callback_query(F.data.startswith("create_now:"))
async def create_nowpayments_invoice(callback: CallbackQuery):
    amount_toman = int(callback.data.split(":", 1)[1])
    usd = toman_to_usd(amount_toman)
    np = NowPaymentsClient()
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        order = Order(user_id=user.id, order_type="topup", amount_toman=amount_toman, status="pending", payment_method="nowpayments")
        session.add(order)
        await session.flush()
        try:
            invoice = await np.create_invoice(order_id=f"topup-{order.id}", description=f"Wallet topup for Telegram user {user.telegram_id}", amount_usd=usd)
        except NowPaymentsError as exc:
            await callback.message.answer(f"خطا در ساخت لینک پرداخت: {html.escape(str(exc))}", reply_markup=back_to_menu_keyboard(user.is_admin))
            await callback.answer()
            return
        order.external_id = str(invoice.get("id") or invoice.get("invoice_id") or "")
        order.payload_json = json.dumps(invoice, ensure_ascii=False)
        payment = Payment(
            user_id=user.id,
            order_id=order.id,
            provider="nowpayments",
            provider_invoice_id=order.external_id,
            amount_toman=amount_toman,
            amount_usd=str(usd),
            status="pending",
            payload_json=json.dumps(invoice, ensure_ascii=False),
        )
        session.add(payment)
        url = invoice.get("invoice_url") or invoice.get("url") or invoice.get("payment_url")
        await callback.message.answer(
            f"مبلغ داخلی: {fmt_toman(amount_toman)}\n"
            f"معادل دلاری با نرخ ثابت شما: {usd} USD\n\n"
            f"لینک پرداخت:\n{url or 'در پاسخ NOWPayments لینک مشخصی پیدا نشد؛ payload سفارش ذخیره شد.'}",
            reply_markup=back_to_menu_keyboard(user.is_admin),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manual_topup:"))
async def manual_topup(callback: CallbackQuery, state: FSMContext):
    settings = get_settings()
    amount = int(callback.data.split(":", 1)[1])
    if not settings.manual_payment_enabled or not settings.card_to_card_number:
        await callback.message.answer("پرداخت کارت‌به‌کارت فعلاً فعال/تنظیم نیست.")
        await callback.answer()
        return
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        order = Order(user_id=user.id, order_type="topup", amount_toman=amount, status="pending", payment_method="manual")
        session.add(order)
        await session.flush()
        await state.update_data(manual_order_id=order.id, manual_amount=amount)
        await state.set_state(TopupStates.waiting_receipt)
        await callback.message.answer(
            f"برای شارژ {fmt_toman(amount)} به کارت زیر واریز کن و عکس رسید را همینجا بفرست:\n\n"
            f"کارت: <code>{html.escape(settings.card_to_card_number)}</code>\n"
            f"به نام: {html.escape(settings.card_to_card_owner or '-')}",
            reply_markup=back_to_menu_keyboard(user.is_admin),
        )
    await callback.answer()


@router.message(TopupStates.waiting_receipt, F.photo)
async def manual_receipt_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = int(data.get("manual_amount", 0))
    order_id = data.get("manual_order_id")
    if amount <= 0:
        await message.answer("مبلغ سفارش مشخص نیست. دوباره از منو شروع کن.")
        await state.clear()
        return
    async with session_scope() as session:
        user = await get_or_create_user(session, message.from_user)
        admin_ids = await get_admin_telegram_ids(session)
        receipt = ManualReceipt(
            user_id=user.id,
            order_id=int(order_id) if order_id else None,
            amount_toman=amount,
            file_id=message.photo[-1].file_id,
            caption=message.caption or "",
            status="pending",
        )
        session.add(receipt)
        await session.flush()
        receipt_id = receipt.id
        is_admin = user.is_admin
        order = await session.get(Order, int(order_id)) if order_id else None
        order_type = order.order_type if order else "topup"
    await state.clear()
    await message.answer("رسیدت ثبت شد؛ بعد از تایید ادمین، سفارش انجام میشه.", reply_markup=back_to_menu_keyboard(is_admin))

    from app.bot.keyboards import admin_receipt_keyboard

    for admin_id in admin_ids:
        try:
            await message.bot.send_photo(
                admin_id,
                photo=message.photo[-1].file_id,
                caption=(
                    f"رسید جدید #{receipt_id}\n"
                    f"نوع سفارش: {order_type}\n"
                    f"کاربر: {message.from_user.id} @{message.from_user.username or '-'}\n"
                    f"مبلغ: {fmt_toman(amount)}\n"
                    f"توضیح کاربر: {message.caption or '-'}"
                ),
                reply_markup=admin_receipt_keyboard(receipt_id),
            )
        except Exception:
            pass


@router.message(TopupStates.waiting_receipt)
async def manual_receipt_need_photo(message: Message):
    await message.answer("لطفاً عکس رسید رو بفرست یا از دکمه منوی اصلی استفاده کن.")


@router.callback_query(F.data.startswith("svc:"))
async def service_detail(callback: CallbackQuery):
    service_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        service = await session.get(Service, service_id)
        if not service or service.user_id != user.id:
            await callback.answer("سرویس پیدا نشد.", show_alert=True)
            return
        await callback.message.answer(_service_text(service), reply_markup=service_detail_keyboard(service.id, user.is_admin))
    await callback.answer()


@router.callback_query(F.data.startswith("svc_link:"))
async def service_link(callback: CallbackQuery):
    service_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        service = await session.get(Service, service_id)
        if not service or service.user_id != user.id:
            await callback.answer("سرویس پیدا نشد.", show_alert=True)
            return
        await callback.message.answer(f"🔗 لینک اشتراک:\n<code>{html.escape(service.link)}</code>", reply_markup=service_detail_keyboard(service.id, user.is_admin))
    await callback.answer()


@router.callback_query(F.data.startswith("svc_qr:"))
async def service_qr(callback: CallbackQuery):
    service_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        user = await get_or_create_user(session, callback.from_user)
        service = await session.get(Service, service_id)
        if not service or service.user_id != user.id:
            await callback.answer("سرویس پیدا نشد.", show_alert=True)
            return
        img = qrcode.make(service.link)
        buf = BytesIO()
        img.save(buf, format="PNG")
        file = BufferedInputFile(buf.getvalue(), filename=f"service-{service.id}.png")
        await callback.message.answer_photo(file, caption=f"QR سرویس #{service.id}", reply_markup=service_detail_keyboard(service.id, user.is_admin))
    await callback.answer()


@router.message(F.text == "📦 سرویس‌های من")
async def my_services(message: Message):
    await _send_services(message, message.from_user)


@router.message(F.text == "📞 پشتیبانی")
async def support(message: Message):
    await _send_support(message, message.from_user)
