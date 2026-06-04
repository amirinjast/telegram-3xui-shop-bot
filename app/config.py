from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_config import load_runtime_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_ids_raw: str = Field("", alias="ADMIN_IDS")
    allow_first_admin_claim: bool = Field(False, alias="ALLOW_FIRST_ADMIN_CLAIM")
    internal_api_key: str = Field("", alias="INTERNAL_API_KEY")

    public_base_url: str = Field("http://localhost:8080", alias="PUBLIC_BASE_URL")
    app_host: str = Field("127.0.0.1", alias="APP_HOST")
    app_port: int = Field(8080, alias="APP_PORT")
    database_url: str = Field("sqlite+aiosqlite:///./shop.db", alias="DATABASE_URL")

    local_currency_unit: str = Field("toman", alias="LOCAL_CURRENCY_UNIT")
    usd_rate_toman: int = Field(150_000, alias="USD_RATE_TOMAN")
    min_topup_toman: int = Field(50_000, alias="MIN_TOPUP_TOMAN")

    nowpayments_enabled: bool = Field(True, alias="NOWPAYMENTS_ENABLED")
    nowpayments_api_key: str = Field("", alias="NOWPAYMENTS_API_KEY")
    nowpayments_ipn_secret: str = Field("", alias="NOWPAYMENTS_IPN_SECRET")
    nowpayments_base_url: str = Field("https://api.nowpayments.io/v1", alias="NOWPAYMENTS_BASE_URL")
    nowpayments_price_currency: str = Field("usd", alias="NOWPAYMENTS_PRICE_CURRENCY")
    nowpayments_pay_currency: str = Field("", alias="NOWPAYMENTS_PAY_CURRENCY")
    nowpayments_success_statuses_raw: str = Field("finished,confirmed", alias="NOWPAYMENTS_SUCCESS_STATUSES")

    manual_payment_enabled: bool = Field(True, alias="MANUAL_PAYMENT_ENABLED")
    card_to_card_number: str = Field("", alias="CARD_TO_CARD_NUMBER")
    card_to_card_owner: str = Field("", alias="CARD_TO_CARD_OWNER")

    xui_base_url: str = Field("", alias="XUI_BASE_URL")
    xui_auth_header: str = Field("Authorization", alias="XUI_AUTH_HEADER")
    xui_auth_prefix: str = Field("Bearer", alias="XUI_AUTH_PREFIX")
    xui_api_key: str = Field("", alias="XUI_API_KEY")
    xui_api_prefix: str = Field("/panel/api", alias="XUI_API_PREFIX")
    xui_subscription_base_url: str = Field("", alias="XUI_SUBSCRIPTION_BASE_URL")
    xui_tls_verify: bool = Field(True, alias="XUI_TLS_VERIFY")
    xui_default_inbound_id: int = Field(1, alias="XUI_DEFAULT_INBOUND_ID")
    xui_default_limit_ip: int = Field(0, alias="XUI_DEFAULT_LIMIT_IP")
    xui_default_flow: str = Field("", alias="XUI_DEFAULT_FLOW")

    trial_enabled: bool = Field(False, alias="TRIAL_ENABLED")
    expire_warning_hours: int = Field(6, alias="EXPIRE_WARNING_CHECK_HOURS")

    @computed_field
    @property
    def admin_ids(self) -> List[int]:
        return [int(x.strip()) for x in self.admin_ids_raw.split(",") if x.strip().isdigit()]

    @computed_field
    @property
    def nowpayments_success_statuses(self) -> set[str]:
        return {s.strip().lower() for s in self.nowpayments_success_statuses_raw.split(",") if s.strip()}

    @property
    def nowpayments_ipn_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/webhooks/nowpayments"


def _apply_runtime_overrides(settings: Settings) -> Settings:
    data = load_runtime_config()
    aliases = {field.alias: name for name, field in settings.__class__.model_fields.items() if field.alias}
    for alias, value in data.items():
        name = aliases.get(alias)
        if name:
            setattr(settings, name, value)
    return settings


@lru_cache
def get_settings() -> Settings:
    return _apply_runtime_overrides(Settings())


def reload_settings() -> None:
    get_settings.cache_clear()
