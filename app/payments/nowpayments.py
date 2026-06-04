from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx

from app.config import get_settings


class NowPaymentsError(RuntimeError):
    pass


class NowPaymentsClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.nowpayments_base_url.rstrip("/")

    @property
    def enabled(self) -> bool:
        return self.settings.nowpayments_enabled and bool(self.settings.nowpayments_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.settings.nowpayments_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def check_api_key(self) -> dict[str, Any]:
        if not self.enabled:
            raise NowPaymentsError("NOWPayments is disabled or API key is missing")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/merchant/coins", headers=self._headers())
        if resp.status_code >= 400:
            raise NowPaymentsError(f"NOWPayments HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def create_invoice(
        self,
        *,
        order_id: str,
        description: str,
        amount_usd: Decimal,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise NowPaymentsError("NOWPayments is disabled or API key is missing")

        payload: dict[str, Any] = {
            "price_amount": float(amount_usd),
            "price_currency": self.settings.nowpayments_price_currency.lower(),
            "order_id": order_id,
            "order_description": description,
            "ipn_callback_url": self.settings.nowpayments_ipn_url,
        }
        if self.settings.nowpayments_pay_currency.strip():
            payload["pay_currency"] = self.settings.nowpayments_pay_currency.strip().lower()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base_url}/invoice", headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise NowPaymentsError(f"NOWPayments HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def get_payment_status(self, payment_id: str) -> dict[str, Any]:
        if not self.enabled:
            raise NowPaymentsError("NOWPayments is disabled or API key is missing")
        if not payment_id:
            raise NowPaymentsError("NOWPayments payment_id is missing")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/payment/{payment_id}", headers=self._headers())
        if resp.status_code >= 400:
            raise NowPaymentsError(f"NOWPayments HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def trusted_ipn_payload(self, payload: dict[str, Any], received_sig: str | None) -> dict[str, Any]:
        if self.settings.nowpayments_ipn_secret:
            if not self.verify_ipn(payload, received_sig):
                raise NowPaymentsError("Invalid NOWPayments IPN signature")
            return payload

        payment_id = str(payload.get("payment_id") or payload.get("id") or "")
        if not payment_id:
            raise NowPaymentsError("NOWPayments IPN payload has no payment_id")

        trusted_payload = await self.get_payment_status(payment_id)

        for key in ("order_id", "invoice_id"):
            if not trusted_payload.get(key) and payload.get(key):
                trusted_payload[key] = payload[key]

        return trusted_payload

    def verify_ipn(self, payload: dict[str, Any], received_sig: str | None) -> bool:
        if not received_sig or not self.settings.nowpayments_ipn_secret:
            return False
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hmac.new(
            self.settings.nowpayments_ipn_secret.encode(),
            canonical.encode(),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(digest, received_sig)
