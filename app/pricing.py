from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.config import get_settings


def normalize_to_toman(amount: int | str) -> int:
    """Normalize UI/admin input to toman according to LOCAL_CURRENCY_UNIT."""
    settings = get_settings()
    value = int(str(amount).replace(",", "").strip())
    if settings.local_currency_unit.lower() == "rial":
        return value // 10
    return value


def toman_to_usd(amount_toman: int) -> Decimal:
    settings = get_settings()
    if settings.usd_rate_toman <= 0:
        raise ValueError("USD_RATE_TOMAN must be greater than zero")
    return (Decimal(amount_toman) / Decimal(settings.usd_rate_toman)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_toman(amount_toman: int) -> str:
    return f"{amount_toman:,} تومان"
