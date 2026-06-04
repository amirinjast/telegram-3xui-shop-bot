from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _column_exists(conn, table: str, column: str) -> bool:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).mappings().all()
    return any(row["name"] == column for row in rows)


async def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if not await _column_exists(conn, table, column):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


async def ensure_schema_updates() -> None:
    """Small SQLite migrations for people replacing older builds.

    SQLAlchemy's create_all creates missing tables but does not add missing
    columns, so this keeps upgrades phone-friendly.
    """
    async with engine.begin() as conn:
        await _add_column_if_missing(conn, "orders", "service_id", "service_id INTEGER REFERENCES services(id)")
        await _add_column_if_missing(conn, "manual_receipts", "order_id", "order_id INTEGER REFERENCES orders(id)")
        await _add_column_if_missing(conn, "services", "warned_3d", "warned_3d BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "services", "warned_1d", "warned_1d BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "services", "expired_notified", "expired_notified BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "users", "is_reseller", "is_reseller BOOLEAN DEFAULT 0")


async def init_db() -> None:
    # Import models so SQLAlchemy metadata is populated.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_updates()
