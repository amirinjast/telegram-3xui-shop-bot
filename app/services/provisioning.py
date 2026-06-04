from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, Service, User
from app.panels.xui import XUIClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_dt(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def provision_service(session: AsyncSession, *, user: User, plan: Plan) -> Service:
    xui = XUIClient()
    client = await xui.add_client(
        inbound_id=plan.xui_inbound_id,
        telegram_id=user.telegram_id,
        traffic_gb=plan.traffic_gb,
        days=plan.days,
        limit_ip=plan.xui_limit_ip,
        flow=plan.xui_flow,
    )
    service = Service(
        user_id=user.id,
        plan_id=plan.id,
        xui_inbound_id=plan.xui_inbound_id,
        client_uuid=client["client_uuid"],
        email=client["email"],
        sub_id=client["sub_id"],
        traffic_gb=plan.traffic_gb,
        expires_at=utc_now() + timedelta(days=plan.days),
        link=client["subscription_link"],
        status="active",
    )
    session.add(service)
    await session.flush()
    return service


async def renew_service(session: AsyncSession, *, user: User, service: Service, plan: Plan) -> Service:
    """Extend a service and update the matching 3x-ui client.

    If the service is still active, days are added to the old expiry. If it is
    expired, the new period starts now. Traffic is refreshed to the selected
    plan's traffic amount.
    """
    now = utc_now()
    old_expiry = normalize_dt(service.expires_at)
    base = old_expiry if old_expiry > now else now
    new_expiry = base + timedelta(days=plan.days)

    service.plan_id = plan.id
    service.xui_inbound_id = plan.xui_inbound_id
    service.traffic_gb = plan.traffic_gb
    service.expires_at = new_expiry
    service.status = "active"
    service.warned_3d = False
    service.warned_1d = False
    service.expired_notified = False

    xui = XUIClient()
    await xui.update_client(
        inbound_id=plan.xui_inbound_id,
        client_uuid=service.client_uuid,
        email=service.email,
        telegram_id=user.telegram_id,
        sub_id=service.sub_id,
        traffic_gb=plan.traffic_gb,
        expires_at=new_expiry,
        limit_ip=plan.xui_limit_ip,
        flow=plan.xui_flow,
        enable=True,
    )
    await session.flush()
    return service


async def disable_expired_service(session: AsyncSession, *, user: User, service: Service, plan: Plan) -> Service:
    service.status = "expired"
    service.expired_notified = True
    try:
        await XUIClient().disable_client(
            inbound_id=service.xui_inbound_id,
            client_uuid=service.client_uuid,
            email=service.email,
            telegram_id=user.telegram_id,
            sub_id=service.sub_id,
            traffic_gb=service.traffic_gb,
            expires_at=normalize_dt(service.expires_at),
            limit_ip=plan.xui_limit_ip,
            flow=plan.xui_flow,
        )
    except Exception:
        # The DB status should still be correct even if a panel fork has a
        # different update endpoint. Admin can adjust adapter endpoint later.
        pass
    await session.flush()
    return service
