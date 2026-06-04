from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.bot.keyboards import service_detail_keyboard
from app.config import get_settings
from app.db import session_scope
from app.messages import get_text
from app.models import Plan, Service, User
from app.services.provisioning import disable_expired_service, normalize_dt


async def check_expiring_services(bot) -> None:
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        result = await session.execute(select(Service).where(Service.status == "active"))
        services = result.scalars().all()
        for service in services:
            user = await session.get(User, service.user_id)
            plan = await session.get(Plan, service.plan_id)
            if not user or not plan:
                continue
            expires_at = normalize_dt(service.expires_at)
            remaining = expires_at - now
            try:
                if remaining <= timedelta(seconds=0):
                    if not service.expired_notified:
                        text = await get_text(session, "EXPIRED")
                        await disable_expired_service(session, user=user, service=service, plan=plan)
                        await bot.send_message(user.telegram_id, text, reply_markup=service_detail_keyboard(service.id, user.is_admin))
                    continue
                if remaining <= timedelta(days=1) and not service.warned_1d:
                    service.warned_1d = True
                    text = await get_text(session, "EXPIRE_1D")
                    await bot.send_message(user.telegram_id, text, reply_markup=service_detail_keyboard(service.id, user.is_admin))
                    continue
                if remaining <= timedelta(days=3) and not service.warned_3d:
                    service.warned_3d = True
                    text = await get_text(session, "EXPIRE_3D")
                    await bot.send_message(user.telegram_id, text, reply_markup=service_detail_keyboard(service.id, user.is_admin))
            except Exception:
                # Avoid killing the scheduler because a user blocked the bot or a panel fork differs.
                continue


async def periodic_service_tasks(bot) -> None:
    while True:
        try:
            await check_expiring_services(bot)
        except Exception:
            pass
        hours = max(1, int(get_settings().expire_warning_hours or 6))
        await asyncio.sleep(hours * 3600)
