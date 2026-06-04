from __future__ import annotations

import asyncio

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.api import create_app
from app.bot.handlers import admin, admin_sales, user
from app.config import get_settings
from app.db import init_db
from app.runtime_config import ensure_runtime_defaults
from app.tasks import periodic_service_tasks


async def run_api(app, host: str, port: int):
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    settings = get_settings()
    ensure_runtime_defaults()
    # reload after creating generated values such as INTERNAL_API_KEY
    from app.config import reload_settings
    reload_settings()
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # Register sales/admin overrides first so the new plan wizard and active
    # inbound flow handle shared callbacks before the legacy handlers.
    dp.include_router(admin_sales.router)
    dp.include_router(admin.router)
    dp.include_router(user.router)

    app = create_app(bot)
    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        run_api(app, settings.app_host, settings.app_port),
        periodic_service_tasks(bot),
    )
