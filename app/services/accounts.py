from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User


async def any_admin_exists(session: AsyncSession) -> bool:
    result = await session.execute(select(User.id).where(User.is_admin == True).limit(1))  # noqa: E712
    return result.scalar_one_or_none() is not None


async def get_or_create_user(session: AsyncSession, telegram_user) -> User:
    settings = get_settings()
    telegram_id = int(telegram_user.id)
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    env_admin = telegram_id in settings.admin_ids
    no_admin_yet = not await any_admin_exists(session)

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=getattr(telegram_user, "username", None),
            first_name=getattr(telegram_user, "first_name", None),
            is_admin=env_admin or no_admin_yet,
        )
        session.add(user)
        await session.flush()
    else:
        user.username = getattr(telegram_user, "username", None)
        user.first_name = getattr(telegram_user, "first_name", None)
        if env_admin or no_admin_yet:
            user.is_admin = True
    return user


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_admin_telegram_ids(session: AsyncSession) -> list[int]:
    settings = get_settings()
    ids = set(settings.admin_ids)
    result = await session.execute(select(User.telegram_id).where(User.is_admin == True))  # noqa: E712
    ids.update(int(x) for x in result.scalars().all())
    return sorted(ids)
