"""Process configuration. Everything comes from the environment (.env locally, Render env vars in prod).

Hard rule: this app only ever talks to the PayPal SANDBOX. `Settings` refuses to start otherwise.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PayPal (sandbox only)
    paypal_env: str = Field(default="sandbox", alias="PAYPAL_ENV")
    paypal_client_id: str = Field(default="", alias="PAYPAL_CLIENT_ID")
    paypal_client_secret: str = Field(default="", alias="PAYPAL_CLIENT_SECRET")
    paypal_webhook_id: str = Field(default="", alias="PAYPAL_WEBHOOK_ID")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")

    # Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    reasoning_model: str = Field(default="claude-opus-5-5", alias="TK_REASONING_MODEL")
    text_model: str = Field(default="claude-haiku-5-5", alias="TK_TEXT_MODEL")

    # Server secrets
    mandate_hmac_secret: str = Field(default="dev-only-change-me", alias="MANDATE_HMAC_SECRET")
    mcp_demo_token: str = Field(default="", alias="MCP_DEMO_TOKEN")
    session_secret: str = Field(default="dev-only-change-me", alias="SESSION_SECRET")

    # Storage and limits
    db_path: str = Field(default="./data/turnkeeper.db", alias="TK_DB_PATH")
    freeze: bool = Field(default=False, alias="TURNKEEPER_FREEZE")
    paypal_rate_per_min: int = Field(default=40, alias="TK_PAYPAL_RATE_PER_MIN")
    max_active_sessions: int = Field(default=6, alias="TK_MAX_ACTIVE_SESSIONS")

    @field_validator("paypal_env")
    @classmethod
    def _sandbox_only(cls, v: str) -> str:
        if v.lower() != "sandbox":
            raise ValueError("Turnkeeper runs against the PayPal sandbox only (PAYPAL_ENV=sandbox)")
        return "sandbox"

    @property
    def paypal_base_url(self) -> str:
        return SANDBOX_BASE_URL

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
