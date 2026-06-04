from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
import json


TEXTS: dict[str, str] = {
    "WELCOME": "سلام 👋\nاز اینجا می‌تونی سرویس بخری، کیف پولت رو شارژ کنی و لینک اشتراکت رو بگیری.",
    "SUPPORT": "📞 برای پشتیبانی به ادمین پیام بده.",
    "BUY_SUCCESS": "✅ سرویس شما ساخته شد.",
    "RENEW_SUCCESS": "✅ سرویس شما تمدید شد.",
    "EXPIRE_3D": "⏰ کمتر از ۳ روز از سرویس شما باقی مانده. برای تمدید روی دکمه تمدید بزن.",
    "EXPIRE_1D": "⏰ کمتر از ۱ روز از سرویس شما باقی مانده. بهتره همین الان تمدیدش کنی.",
    "EXPIRED": "❌ سرویس شما منقضی شد. برای فعال‌سازی دوباره، تمدیدش کن.",
    "ANDROID_HELP": "📱 اندروید: لینک subscription را داخل v2rayNG یا Hiddify اضافه کن.",
    "IOS_HELP": "📱 آیفون: لینک subscription را داخل Streisand، FoXray یا Hiddify اضافه کن.",
    "WINDOWS_HELP": "💻 ویندوز: لینک subscription را داخل Hiddify یا Nekoray اضافه کن.",
    "RULES": "قوانین خرید: بعد از پرداخت، سرویس به صورت خودکار ساخته می‌شود. مسئولیت نگهداری لینک با کاربر است.",
}


async def get_text(session: AsyncSession, key: str) -> str:
    key = key.upper()
    setting_key = f"TEXT_{key}"
    result = await session.execute(select(AppSetting).where(AppSetting.key == setting_key))
    row = result.scalar_one_or_none()
    if row:
        try:
            return str(json.loads(row.value_json))
        except Exception:
            return row.value_json
    return TEXTS.get(key, "")


async def set_text(session: AsyncSession, key: str, value: str) -> None:
    key = key.upper().replace("TEXT_", "")
    setting_key = f"TEXT_{key}"
    row = await session.get(AppSetting, setting_key)
    payload = json.dumps(value, ensure_ascii=False)
    if row:
        row.value_json = payload
    else:
        session.add(AppSetting(key=setting_key, value_json=payload))
    await session.flush()
