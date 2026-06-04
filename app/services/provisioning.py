from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, Service, User, XuiInbound
from app.panels.xui import XUIClient


class InactiveInboundError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_dt(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def get_active_inbound_for_plan(session: AsyncSession, plan: Plan) -> XuiInbound:
    """Return the active inbound/group that must receive clients for this plan."""
    result = await session.execute(
        select(XuiInbound).where(
            XuiInbound.inbound_id == plan.xui_inbound_id,
            XuiInbound.active == True,  # noqa: E712
        )
    )
    inbound = result.scalars().first()
    if not inbound:
        raise InactiveInboundError(
            f"Inbound {plan.xui_inbound_id} is not active or is not loaded in the bot."
        )
    return inbound


async def provision_service(session: AsyncSession, *, user: User, plan: Plan) -> Service:
    inbound = await get_active_inbound_for_plan(session, plan)
    limit_ip = inbound.limit_ip
    flow = inbound.flow or plan.xui_flow

    xui = XUIClient()
    client = await xui.add_client(
        inbound_id=inbound.inbound_id,
        telegram_id=user.telegram_id,
        traffic_gb=plan.traffic_gb,
        days=plan.days,
        limit_ip=limit_ip,
        flow=flow,
    )
    service = Service(
        user_id=user.id,
        plan_id=plan.id,
        xui_inbound_id=inbound.inbound_id,
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
    plan's traffic amount. The plan inbound must be active so disabled groups
    cannot receive new/renewed clients.
    """
    inbound = await get_active_inbound_for_plan(session, plan)
    limit_ip = inbound.limit_ip
    flow = inbound.flow or plan.xui_flow

    now = utc_now()
    old_expiry = normalize_dt(service.expires_at)
    base = old_expiry if old_expiry > now else now
    new_expiry = base + timedelta(days=plan.days)

    service.plan_id = plan.id
    service.xui_inbound_id = inbound.inbound_id
    service.traffic_gb = plan.traffic_gb
    service.expires_at = new_expiry
    service.status = "active"
    service.warned_3d = False
    service.warned_1d = False
    service.expired_notified = False

    xui = XUIClient()
    await xui.update_client(
        inbound_id=inbound.inbound_id,
        client_uuid=service.client_uuid,
        email=service.email,
        telegram_id=user.telegram_id,
        sub_id=service.sub_id,
        traffic_gb=plan.traffic_gb,
        expires_at=new_expiry,
        limit_ip=limit_ip,
        flow=flow,
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
