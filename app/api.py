from __future__ import annotations

import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Order, Payment, Plan, User
from app.payments.nowpayments import NowPaymentsClient
from app.pricing import toman_to_usd
from app.services.billing import credit_wallet, mark_nowpayments_success


class CreditRequest(BaseModel):
    telegram_id: int
    amount_toman: int = Field(gt=0)
    description: str = "External API credit"


class TopupInvoiceRequest(BaseModel):
    telegram_id: int
    amount_toman: int = Field(gt=0)
    description: str = "Wallet topup"


class BuyInvoiceRequest(BaseModel):
    telegram_id: int
    plan_id: int
    description: str = "Direct service purchase"


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if x_api_key != settings.internal_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def create_app(bot=None) -> FastAPI:
    app = FastAPI(title="Telegram 3x-ui Shop Bot API", version="0.2.0")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/webhooks/nowpayments")
    async def nowpayments_webhook(request: Request):
        payload = await request.json()
        sig = request.headers.get("x-nowpayments-sig")
        client = NowPaymentsClient()
        if not client.verify_ipn(payload, sig):
            raise HTTPException(status_code=401, detail="Invalid IPN signature")
        async with session_scope() as session:
            result = await mark_nowpayments_success(session, payload)
            if result.changed and result.order and bot:
                user = await session.get(User, result.order.user_id)
                if user:
                    try:
                        if result.action == "topup":
                            await bot.send_message(user.telegram_id, f"✅ پرداخت تایید شد و {result.order.amount_toman:,} تومان به کیف پولت اضافه شد.")
                        elif result.service:
                            await bot.send_message(
                                user.telegram_id,
                                "✅ پرداخت تایید شد. سرویس آماده است:\n\n"
                                f"ایمیل/نام کلاینت: <code>{result.service.email}</code>\n"
                                f"لینک اشتراک:\n<code>{result.service.link}</code>",
                            )
                    except Exception:
                        pass
        return {"ok": True, "changed": result.changed, "action": result.action}

    @app.post("/api/v1/wallet/credit", dependencies=[Depends(require_api_key)])
    async def api_credit_wallet(data: CreditRequest):
        async with session_scope() as session:
            result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(telegram_id=data.telegram_id)
                session.add(user)
                await session.flush()
            tx = await credit_wallet(
                session,
                user=user,
                amount_toman=data.amount_toman,
                tx_type="api_credit",
                ref_type="external_api",
                description=data.description,
            )
            return {"ok": True, "transaction_id": tx.id, "balance_toman": user.balance_toman}

    async def _create_invoice_for_order(session, user: User, order: Order, description: str):
        np = NowPaymentsClient()
        usd = toman_to_usd(order.amount_toman)
        invoice = await np.create_invoice(
            order_id=f"{order.order_type}-{order.id}",
            description=description,
            amount_usd=usd,
        )
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
        return usd, invoice

    @app.post("/api/v1/topup/nowpayments", dependencies=[Depends(require_api_key)])
    async def api_create_topup_invoice(data: TopupInvoiceRequest):
        async with session_scope() as session:
            result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(telegram_id=data.telegram_id)
                session.add(user)
                await session.flush()
            order = Order(user_id=user.id, order_type="topup", amount_toman=data.amount_toman, status="pending")
            session.add(order)
            await session.flush()
            usd, invoice = await _create_invoice_for_order(session, user, order, data.description)
            return {"ok": True, "order_id": order.id, "amount_toman": data.amount_toman, "amount_usd": str(usd), "invoice": invoice}

    @app.post("/api/v1/buy/nowpayments", dependencies=[Depends(require_api_key)])
    async def api_create_buy_invoice(data: BuyInvoiceRequest):
        async with session_scope() as session:
            user = (await session.execute(select(User).where(User.telegram_id == data.telegram_id))).scalar_one_or_none()
            if not user:
                user = User(telegram_id=data.telegram_id)
                session.add(user)
                await session.flush()
            plan = await session.get(Plan, data.plan_id)
            if not plan or not plan.active:
                raise HTTPException(status_code=404, detail="Plan not found or inactive")
            order = Order(user_id=user.id, plan_id=plan.id, order_type="buy", amount_toman=plan.price_toman, status="pending")
            session.add(order)
            await session.flush()
            usd, invoice = await _create_invoice_for_order(session, user, order, data.description)
            return {"ok": True, "order_id": order.id, "amount_toman": plan.price_toman, "amount_usd": str(usd), "invoice": invoice}

    return app
