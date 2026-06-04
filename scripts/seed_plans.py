import asyncio

from app.db import init_db, session_scope
from app.models import Plan


async def main():
    await init_db()
    async with session_scope() as session:
        # Example: 150,000 toman with USD_RATE_TOMAN=150,000 => 1 USD for NOWPayments.
        session.add_all(
            [
                Plan(name="20 گیگ یک‌ماهه", traffic_gb=20, days=30, price_toman=150_000, xui_inbound_id=1),
                Plan(name="50 گیگ یک‌ماهه", traffic_gb=50, days=30, price_toman=300_000, xui_inbound_id=1),
                Plan(name="100 گیگ دوماهه", traffic_gb=100, days=60, price_toman=550_000, xui_inbound_id=1),
            ]
        )
    print("Seeded example plans")


if __name__ == "__main__":
    asyncio.run(main())
