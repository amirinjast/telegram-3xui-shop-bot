from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ManualReceipt, Order, Payment, Plan, Service, Transaction, User


@dataclass
class SettlementResult:
    changed: bool
    payment: Payment | None = None
    order: Order | None = None
    service: Service | None = None
    action: str = ""  # topup, buy, renew, ignored
    message: str = ""


async def credit_wallet(
    session: AsyncSession,
    *,
    user: User,
    amount_toman: int,
    tx_type: str,
    ref_type: str = "",
    ref_id: int | None = None,
    description: str = "",
) -> Transaction:
    user.balance_toman += amount_toman
    tx = Transaction(
        user_id=user.id,
        amount_toman=amount_toman,
        tx_type=tx_type,
        ref_type=ref_type,
        ref_id=ref_id,
        description=description,
    )
    session.add(tx)
    await session.flush()
    return tx


async def debit_wallet(
    session: AsyncSession,
    *,
    user: User,
    amount_toman: int,
    tx_type: str,
    ref_type: str = "",
    ref_id: int | None = None,
    description: str = "",
) -> Transaction:
    if user.balance_toman < amount_toman:
        raise ValueError("Insufficient wallet balance")
    user.balance_toman -= amount_toman
    tx = Transaction(
        user_id=user.id,
        amount_toman=-amount_toman,
        tx_type=tx_type,
        ref_type=ref_type,
        ref_id=ref_id,
        description=description,
    )
    session.add(tx)
    await session.flush()
    return tx


async def complete_paid_order(session: AsyncSession, *, order: Order) -> SettlementResult:
    """Apply a paid order exactly once.

    topup -> credits wallet.
    buy   -> provisions a new 3x-ui service.
    renew -> extends the selected service and updates 3x-ui.
    """
    if order.status == "completed":
        service = await session.get(Service, order.service_id) if order.service_id else None
        return SettlementResult(False, order=order, service=service, action="ignored", message="Order already completed")

    user = await session.get(User, order.user_id)
    if not user:
        raise ValueError("User not found")

    order.status = "paid"

    if order.order_type == "topup":
        await credit_wallet(
            session,
            user=user,
            amount_toman=order.amount_toman,
            tx_type="topup",
            ref_type="order",
            ref_id=order.id,
            description="شارژ کیف پول",
        )
        order.status = "completed"
        await session.flush()
        return SettlementResult(True, order=order, action="topup", message="Wallet credited")

    if order.order_type == "buy":
        if not order.plan_id:
            raise ValueError("Buy order has no plan")
        plan = await session.get(Plan, order.plan_id)
        if not plan:
            raise ValueError("Plan not found")
        from app.services.provisioning import provision_service

        service = await provision_service(session, user=user, plan=plan)
        order.service_id = service.id
        order.status = "completed"
        await session.flush()
        return SettlementResult(True, order=order, service=service, action="buy", message="Service provisioned")

    if order.order_type == "renew":
        if not order.plan_id or not order.service_id:
            raise ValueError("Renew order has no plan/service")
        plan = await session.get(Plan, order.plan_id)
        service = await session.get(Service, order.service_id)
        if not plan or not service:
            raise ValueError("Plan or service not found")
        from app.services.provisioning import renew_service

        renewed = await renew_service(session, user=user, service=service, plan=plan)
        order.status = "completed"
        await session.flush()
        return SettlementResult(True, order=order, service=renewed, action="renew", message="Service renewed")

    raise ValueError(f"Unknown order type: {order.order_type}")


async def mark_nowpayments_success(session: AsyncSession, payload: dict) -> SettlementResult:
    """Settle a NOWPayments IPN.

    This supports both wallet topups and direct buy/renew invoices.
    """
    from app.config import get_settings

    settings = get_settings()
    payment_id = str(payload.get("payment_id") or payload.get("id") or "")
    invoice_id = str(payload.get("invoice_id") or "") if payload.get("invoice_id") else None
    order_id_text = str(payload.get("order_id") or "")
    status = str(payload.get("payment_status") or "").lower()

    payment: Payment | None = None
    if payment_id:
        result = await session.execute(
            select(Payment).where(Payment.provider == "nowpayments", Payment.provider_payment_id == payment_id)
        )
        payment = result.scalar_one_or_none()
    if payment is None and order_id_text:
        # Bot-created order ids look like topup-123, buy-123, renew-123.
        try:
            _, oid = order_id_text.split("-", 1)
            order_id = int(oid)
        except Exception:
            order_id = None
        if order_id:
            result = await session.execute(
                select(Payment).where(Payment.provider == "nowpayments", Payment.order_id == order_id)
            )
            payment = result.scalar_one_or_none()

    if payment is None:
        return SettlementResult(False, action="ignored", message="Payment not found")

    payment.provider_payment_id = payment_id or payment.provider_payment_id
    payment.provider_invoice_id = invoice_id or payment.provider_invoice_id
    payment.payload_json = json.dumps(payload, ensure_ascii=False)

    if status not in settings.nowpayments_success_statuses:
        payment.status = status or payment.status
        return SettlementResult(False, payment=payment, action="ignored", message=f"Payment status is {status}")

    if payment.status == "paid":
        order = await session.get(Order, payment.order_id) if payment.order_id else None
        return SettlementResult(False, payment=payment, order=order, action="ignored", message="Payment already paid")

    payment.status = "paid"
    order = await session.get(Order, payment.order_id) if payment.order_id else None
    if not order:
        return SettlementResult(False, payment=payment, action="ignored", message="Order not found")

    result = await complete_paid_order(session, order=order)
    result.payment = payment
    return result


async def approve_manual_receipt(
    session: AsyncSession, *, receipt_id: int, admin_telegram_id: int
) -> tuple[ManualReceipt, SettlementResult]:
    receipt = await session.get(ManualReceipt, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")
    if receipt.status != "pending":
        order = await session.get(Order, receipt.order_id) if receipt.order_id else None
        service = await session.get(Service, order.service_id) if order and order.service_id else None
        return receipt, SettlementResult(False, order=order, service=service, action="ignored", message="Receipt already reviewed")

    user = await session.get(User, receipt.user_id)
    if not user:
        raise ValueError("User not found")

    receipt.status = "approved"
    receipt.reviewed_by = admin_telegram_id
    receipt.reviewed_at = datetime.now(timezone.utc)

    if receipt.order_id:
        order = await session.get(Order, receipt.order_id)
        if not order:
            raise ValueError("Order not found")
        order.payment_method = "manual"
        result = await complete_paid_order(session, order=order)
    else:
        await credit_wallet(
            session,
            user=user,
            amount_toman=receipt.amount_toman,
            tx_type="manual_topup",
            ref_type="manual_receipt",
            ref_id=receipt.id,
            description="شارژ کیف پول با تایید رسید کارت‌به‌کارت",
        )
        result = SettlementResult(True, order=None, action="topup", message="Wallet credited")
    return receipt, result


async def reject_manual_receipt(session: AsyncSession, *, receipt_id: int, admin_telegram_id: int) -> ManualReceipt:
    receipt = await session.get(ManualReceipt, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")
    receipt.status = "rejected"
    receipt.reviewed_by = admin_telegram_id
    receipt.reviewed_at = datetime.now(timezone.utc)
    if receipt.order_id:
        order = await session.get(Order, receipt.order_id)
        if order and order.status == "pending":
            order.status = "rejected"
    return receipt
