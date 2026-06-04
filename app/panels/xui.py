from __future__ import annotations

import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import get_settings
from app.runtime_config import load_runtime_config


class XUIError(RuntimeError):
    pass


class XUIClient:
    """3x-ui adapter.

    MHSanaei/3x-ui can run behind a custom webBasePath. If the bot calls
    https://127.0.0.1:25566/panel/api/... while the panel actually lives at
    /some-secret-path/panel/api/..., the panel returns HTTP 404. This client
    therefore tries the configured base URL plus the local 3x-ui webBasePath
    read from the panel database when possible.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.xui_base_url.rstrip("/")
        self.api_prefix = self.settings.xui_api_prefix.strip().strip("/")

    def _headers(self) -> dict[str, str]:
        token = self.settings.xui_api_key.strip()
        prefix = self.settings.xui_auth_prefix.strip()
        value = f"{prefix} {token}".strip() if prefix else token
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.settings.xui_auth_header.strip() and value:
            headers[self.settings.xui_auth_header.strip()] = value
        return headers

    def _api_prefix_candidates(self) -> list[str]:
        candidates: list[str] = []
        for prefix in (
            self.api_prefix,
            "panel/api",
            "api",
            "xui/API",
            "xui/api",
            "",
        ):
            normalized = prefix.strip().strip("/")
            if normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _read_local_xui_setting(self, key: str) -> str:
        env_key = f"XUI_{key.upper()}"
        if os.getenv(env_key):
            return os.getenv(env_key, "").strip()

        runtime = load_runtime_config()
        runtime_value = runtime.get(env_key)
        if runtime_value:
            return str(runtime_value).strip()

        db_candidates = [
            os.getenv("XUI_DB_PATH", ""),
            "/etc/x-ui/x-ui.db",
            "/usr/local/x-ui/bin/x-ui.db",
            "/usr/local/x-ui/x-ui.db",
        ]
        for raw_path in db_candidates:
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.exists():
                continue
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    row = conn.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,)).fetchone()
                finally:
                    conn.close()
            except Exception:
                continue
            if row and row[0]:
                return str(row[0]).strip()
        return ""

    def _base_candidates(self) -> list[str]:
        base = self.base_url.rstrip("/")
        candidates: list[str] = []

        def add(value: str) -> None:
            value = value.rstrip("/")
            if value and value not in candidates:
                candidates.append(value)

        add(base)

        parsed = urlsplit(base)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        add(origin)

        # If 3x-ui has a secret/custom webBasePath, API routes are under it.
        web_base_path = self._read_local_xui_setting("webBasePath").strip("/")
        if web_base_path:
            add(f"{origin}/{web_base_path}")

        # If admin put the path into XUI_API_PREFIX instead of XUI_BASE_URL,
        # keep that usable too. Example: XUI_API_PREFIX=/secret/panel/api.
        prefix_parts = self.api_prefix.split("/")
        if len(prefix_parts) > 2 and prefix_parts[-2:] == ["panel", "api"]:
            add(f"{origin}/{'/'.join(prefix_parts[:-2])}")

        return candidates

    def _url(self, base: str, path: str, api_prefix: str) -> str:
        base = base.rstrip("/")
        path = path.strip("/")
        prefix = api_prefix.strip().strip("/")
        if not prefix:
            return f"{base}/{path}"

        if base.lower().endswith("/" + prefix.lower()):
            return f"{base}/{path}"

        if prefix.lower().endswith("/api"):
            prefix_without_api = prefix[:-4].rstrip("/")
            if prefix_without_api and base.lower().endswith("/" + prefix_without_api.lower()):
                return f"{base}/api/{path}"

        return f"{base}/{prefix}/{path}"

    def _url_candidates(self, path: str) -> list[str]:
        urls: list[str] = []
        for base in self._base_candidates():
            for api_prefix in self._api_prefix_candidates():
                url = self._url(base, path, api_prefix)
                if url not in urls:
                    urls.append(url)
        return urls

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.base_url or not self.settings.xui_api_key:
            raise XUIError("3x-ui URL/API token is not configured")

        errors: list[str] = []
        async with httpx.AsyncClient(timeout=30, verify=False, follow_redirects=True) as client:
            for url in self._url_candidates(path):
                try:
                    resp = await client.request(method, url, headers=self._headers(), **kwargs)
                except httpx.HTTPError as exc:
                    errors.append(f"{url} -> {exc.__class__.__name__}: {str(exc)[:200]}")
                    continue

                if resp.status_code >= 400:
                    errors.append(f"{url} -> HTTP {resp.status_code}: {resp.text[:200]}")
                    continue

                try:
                    data = resp.json()
                except Exception:
                    errors.append(f"{url} -> non-JSON response: {resp.text[:200]}")
                    continue

                if isinstance(data, dict) and data.get("success") is False:
                    msg = data.get("msg") or data.get("message") or "3x-ui API returned success=false"
                    errors.append(f"{url} -> {msg}")
                    continue

                return data

        tried = "\n".join(errors[-12:]) if errors else "No request was attempted"
        hint = (
            "\nHint: HTTP 404 usually means the 3x-ui webBasePath is missing. "
            "Put the full local panel path in XUI_BASE_URL, for example "
            "https://127.0.0.1:25566/YOUR_PANEL_PATH, or set XUI_DB_PATH to the 3x-ui sqlite database."
        )
        raise XUIError(f"3x-ui request failed. Tried:\n{tried}{hint}")

    @staticmethod
    def extract_inbounds(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        if not isinstance(data, dict):
            return []

        for key in ("obj", "data", "inbounds", "result", "rows", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = XUIClient.extract_inbounds(value)
                if nested:
                    return nested

        if data.get("id") is not None and ("settings" in data or "protocol" in data or "remark" in data):
            return [data]

        return []

    async def list_inbounds(self) -> dict[str, Any]:
        errors: list[str] = []
        for endpoint in ("inbounds/options", "inbounds/list/slim", "inbounds/list"):
            try:
                data = await self._request("GET", endpoint)
            except XUIError as exc:
                errors.append(str(exc))
                continue
            inbounds = self.extract_inbounds(data)
            if inbounds:
                return {"obj": inbounds, "raw": data}
        detail = "\n".join(errors[-3:])
        raise XUIError(f"No inbounds returned by 3x-ui. {detail}")

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

        current_body = {"client": client_payload, "inboundIds": [inbound_id]}
        try:
            await self._request("POST", "clients/add", json=current_body)
        except XUIError as current_error:
            legacy_body = {"id": inbound_id, "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":"))}
            try:
                await self._request("POST", "inbounds/addClient", json=legacy_body)
            except XUIError as legacy_error:
                raise XUIError(
                    "Failed to add 3x-ui client with current and legacy APIs.\n"
                    f"clients/add error:\n{current_error}\n\ninbounds/addClient error:\n{legacy_error}"
                ) from legacy_error

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
        try:
            return await self._request("POST", f"clients/update/{email}?inboundIds={inbound_id}", json=client_payload)
        except XUIError:
            legacy_body = {"id": inbound_id, "settings": json.dumps({"clients": [client_payload]}, separators=(",", ":"))}
            return await self._request("POST", f"inbounds/updateClient/{client_uuid}", json=legacy_body)

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
        runtime = load_runtime_config()
        template = (
            os.getenv("XUI_SUBSCRIPTION_PATH_TEMPLATE")
            or str(runtime.get("XUI_SUBSCRIPTION_PATH_TEMPLATE") or "")
            or getattr(self.settings, "xui_subscription_path_template", "/sub/{sub_id}")
            or "/sub/{sub_id}"
        )
        path = template.replace("{sub_id}", sub_id).replace("{subId}", sub_id).strip()
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    async def delete_client(self, inbound_id: int, client_uuid: str) -> Any:
        del inbound_id
        try:
            return await self._request("POST", f"clients/del/{client_uuid}")
        except XUIError:
            return await self._request("POST", f"inbounds/{inbound_id}/delClient/{client_uuid}")

    async def reset_client_traffic(self, inbound_id: int, email: str) -> Any:
        del inbound_id
        try:
            return await self._request("POST", f"clients/resetTraffic/{email}")
        except XUIError:
            return await self._request("POST", f"inbounds/{inbound_id}/resetClientTraffic/{email}")
