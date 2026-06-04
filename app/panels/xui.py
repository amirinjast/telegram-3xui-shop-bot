from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings


class XUIError(RuntimeError):
    pass


class XUIClient:
    """Small 3x-ui adapter with configurable API-key header.

    Most current 3x-ui builds use Authorization: Bearer <token>. Some forks use
    X-API-Key. The bot keeps both header name and prefix configurable from the
    Telegram setup wizard.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.xui_base_url.rstrip("/")
        self.api_prefix = "/" + self.settings.xui_api_prefix.strip("/")

    def _headers(self) -> dict[str, str]:
        token = self.settings.xui_api_key.strip()
        prefix = self.settings.xui_auth_prefix.strip()
        value = f"{prefix} {token}".strip() if prefix else token
        return {
            self.settings.xui_auth_header: value,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}/{path.strip('/')}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.base_url or not self.settings.xui_api_key:
            raise XUIError("3x-ui URL/API key is not configured")
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.request(method, self._url(path), headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            raise XUIError(f"3x-ui HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise XUIError(f"3x-ui returned non-JSON response: {resp.text[:300]}") from exc
        if isinstance(data, dict) and data.get("success") is False:
            raise XUIError(data.get("msg") or data.get("message") or "3x-ui API returned success=false")
        return data

    async def list_inbounds(self) -> Any:
        return await self._request("GET", "inbounds/list")

    def _client_payload(
        self,
        *,
        client_uuid: str,
        email: str,
        telegram_id: int,
        sub_id: str,
        traffic_gb: int,
        expires_at: datetime,
        limit_ip: int = 0,
        flow: str = "",
        enable: bool = True,
    ) -> dict[str, Any]:
        expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        expire_ms = int(expires_at.timestamp() * 1000)
        total_bytes = int(traffic_gb * 1024**3)
        return {
            "id": client_uuid,
            "flow": flow or self.settings.xui_default_flow,
            "email": email,
            "limitIp": limit_ip,
            "totalGB": total_bytes,
            "expiryTime": expire_ms,
            "enable": enable,
            "tgId": str(telegram_id),
            "subId": sub_id,
        }

    async def add_client(
        self,
        *,
        inbound_id: int,
        telegram_id: int,
        traffic_gb: int,
        days: int,
        limit_ip: int = 0,
        flow: str = "",
    ) -> dict[str, str]:
        client_uuid = str(uuid.uuid4())
        sub_id = secrets.token_urlsafe(16)
        email = f"tg-{telegram_id}-{secrets.token_hex(3)}"
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        client_payload = self._client_payload(
            client_uuid=client_uuid,
            email=email,
            telegram_id=telegram_id,
            sub_id=sub_id,
            traffic_gb=traffic_gb,
            expires_at=expires_at,
            limit_ip=limit_ip,
            flow=flow,
            enable=True,
        )
        body = {"id": inbound_id, "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":"))}
        await self._request("POST", "inbounds/addClient", json=body)
        return {
            "client_uuid": client_uuid,
            "email": email,
            "sub_id": sub_id,
            "expire_ms": str(int(expires_at.timestamp() * 1000)),
            "subscription_link": self.subscription_link(sub_id),
        }

    async def update_client(
        self,
        *,
        inbound_id: int,
        client_uuid: str,
        email: str,
        telegram_id: int,
        sub_id: str,
        traffic_gb: int,
        expires_at: datetime,
        limit_ip: int = 0,
        flow: str = "",
        enable: bool = True,
    ) -> Any:
        client_payload = self._client_payload(
            client_uuid=client_uuid,
            email=email,
            telegram_id=telegram_id,
            sub_id=sub_id,
            traffic_gb=traffic_gb,
            expires_at=expires_at,
            limit_ip=limit_ip,
            flow=flow,
            enable=enable,
        )
        body = {"id": inbound_id, "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":"))}
        # Common 3x-ui API shape. If a fork differs, only this adapter needs editing.
        return await self._request("POST", f"inbounds/updateClient/{client_uuid}", json=body)

    async def disable_client(
        self,
        *,
        inbound_id: int,
        client_uuid: str,
        email: str,
        telegram_id: int,
        sub_id: str,
        traffic_gb: int,
        expires_at: datetime,
        limit_ip: int = 0,
        flow: str = "",
    ) -> Any:
        return await self.update_client(
            inbound_id=inbound_id,
            client_uuid=client_uuid,
            email=email,
            telegram_id=telegram_id,
            sub_id=sub_id,
            traffic_gb=traffic_gb,
            expires_at=expires_at,
            limit_ip=limit_ip,
            flow=flow,
            enable=False,
        )

    def subscription_link(self, sub_id: str) -> str:
        base = (self.settings.xui_subscription_base_url or self.settings.xui_base_url).rstrip("/")
        return f"{base}/sub/{sub_id}"

    async def delete_client(self, inbound_id: int, client_uuid: str) -> Any:
        return await self._request("POST", f"inbounds/{inbound_id}/delClient/{client_uuid}")

    async def reset_client_traffic(self, inbound_id: int, email: str) -> Any:
        return await self._request("POST", f"inbounds/{inbound_id}/resetClientTraffic/{email}")
