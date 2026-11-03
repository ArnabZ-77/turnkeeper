"""Process configuration. Everything comes from the environment (.env locally, Render env vars in prod).

Hard rule: this app only ever talks to the PayPal SANDBOX. `Settings` refuses to start otherwise.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"

# Provider-agnostic LLM layer (app/agent/llm.py). Free providers first; Anthropic is an optional switch.
LLM_PROVIDERS = ("none", "anthropic", "gemini", "groq", "openai_compat")
LLM_CACHE_MODES = ("off", "record", "replay", "auto")
DEFAULT_FREE_PROVIDER = "groq"
LLM_DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.8-flash",
    "openai_compat": "",
}
LLM_DEFAULT_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai_compat": "",
}


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

    # LLM provider switch (see .env.example). Empty provider = infer from the keys that are set.
    llm_provider_setting: str = Field(default="", alias="TK_LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="TK_LLM_API_KEY")
    llm_base_url_setting: str = Field(default="", alias="TK_LLM_BASE_URL")
    llm_model_setting: str = Field(default="", alias="TK_LLM_MODEL")
    llm_max_tokens_setting: int = Field(default=0, alias="TK_LLM_MAX_TOKENS")
    llm_reasoning_effort: str = Field(default="", alias="TK_LLM_REASONING_EFFORT")
    llm_cache: str = Field(default="auto", alias="TK_LLM_CACHE")
    live_llm_per_ip_hour: int = Field(default=10, alias="TK_LIVE_LLM_PER_IP_HOUR")

    # Server secrets
    mandate_hmac_secret: str = Field(default="dev-only-change-me", alias="MANDATE_HMAC_SECRET")
    mcp_demo_token: str = Field(default="", alias="MCP_DEMO_TOKEN")
    session_secret: str = Field(default="dev-only-change-me", alias="SESSION_SECRET")

    # Storage and limits
    db_path: str = Field(default="./data/turnkeeper.db", alias="TK_DB_PATH")
    freeze: bool = Field(default=False, alias="TURNKEEPER_FREEZE")
    paypal_rate_per_min: int = Field(default=40, alias="TK_PAYPAL_RATE_PER_MIN")
    max_active_sessions: int = Field(default=6, alias="TK_MAX_ACTIVE_SESSIONS")
    # New anonymous sessions one IP may open per hour. Kept below max_active_sessions so one address can never
    # fill every live slot and lock real visitors out (they fall back to the read-only replay).
    session_creates_per_ip_hour: int = Field(default=5, alias="TK_SESSION_CREATES_PER_IP_HOUR")
    # Browser hardening: "enforce" | "report-only" | "off" for the Content-Security-Policy header.
    csp_mode: str = Field(default="enforce", alias="TK_CSP_MODE")

    # Member mandates + member agents (docs/CONTRACTS.md "Member mandates"). Default OFF. TK_MEMBER_MANDATES turns the
    # feature on for every new circle (a circle can also switch it on alone with its ``member_mandates`` flag);
    # TK_MEMBER_AGENTS=0 is the global kill switch (routes answer 404, mandates stop being required).
    member_mandates: bool = Field(default=False, alias="TK_MEMBER_MANDATES")
    member_agents_switch: str = Field(default="", alias="TK_MEMBER_AGENTS")

    # Judge mini-circle payee (optional sandbox values; empty is fine)
    judge_payee_receiver: str = Field(
        default="", alias="TK_JUDGE_PAYEE_RECEIVER"
    )  # judge.payee sandbox email
    judge_payee_vault_id: str = Field(default="", alias="TK_JUDGE_PAYEE_VAULT_ID")

    @field_validator("llm_provider_setting")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v and v not in LLM_PROVIDERS:
            raise ValueError(f"TK_LLM_PROVIDER must be one of {', '.join(LLM_PROVIDERS)}")
        return v

    @field_validator("llm_max_tokens_setting", "live_llm_per_ip_hour", mode="before")
    @classmethod
    def _blank_int_is_default(cls, v: object, info: object) -> object:
        if isinstance(v, str) and not v.strip():
            return 0 if getattr(info, "field_name", "") == "llm_max_tokens_setting" else 10
        return v

    @field_validator("llm_cache")
    @classmethod
    def _known_cache_mode(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        if v not in LLM_CACHE_MODES:
            raise ValueError(f"TK_LLM_CACHE must be one of {', '.join(LLM_CACHE_MODES)}")
        return v

    @field_validator("paypal_env")
    @classmethod
    def _sandbox_only(cls, v: str) -> str:
        if v.lower() != "sandbox":
            raise ValueError("Turnkeeper runs against the PayPal sandbox only (PAYPAL_ENV=sandbox)")
        return "sandbox"

    @property
    def member_agents_killed(self) -> bool:
        """TK_MEMBER_AGENTS=0 (or false/off/no) switches the whole member-mandate feature off."""
        return self.member_agents_switch.strip().lower() in ("0", "false", "off", "no")

    @property
    def paypal_base_url(self) -> str:
        return SANDBOX_BASE_URL

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    # --- LLM provider resolution -------------------------------------------------------------------------

    @property
    def llm_provider(self) -> str:
        """The effective provider: TK_LLM_PROVIDER when set, else inferred from the keys, else "none"."""
        if self.llm_provider_setting:
            return self.llm_provider_setting
        key = self.llm_api_key
        if key:
            if key.startswith("AIza"):
                return "gemini"
            if key.startswith("sk-ant-"):
                return "anthropic"
            if self.llm_base_url_setting:
                return "openai_compat"
            return DEFAULT_FREE_PROVIDER
        if self.anthropic_api_key:
            return "anthropic"
        return "none"

    @property
    def llm_key(self) -> str:
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key or self.llm_api_key
        return self.llm_api_key

    @property
    def llm_model(self) -> str:
        provider = self.llm_provider
        if self.llm_model_setting:
            return self.llm_model_setting
        if provider == "anthropic":
            return self.reasoning_model
        if provider == "none":
            return ""
        return LLM_DEFAULT_MODELS.get(provider, "")

    @property
    def llm_base_url(self) -> str:
        return self.llm_base_url_setting or LLM_DEFAULT_BASE_URLS.get(self.llm_provider, "")

    @property
    def llm_max_tokens(self) -> int:
        if self.llm_max_tokens_setting > 0:
            return self.llm_max_tokens_setting
        # Anthropic keeps the M1 value; free tiers meter tokens per minute, so ask for less.
        return 8000 if self.llm_provider == "anthropic" else 4096

    @property
    def llm_configured(self) -> bool:
        """True when a live model can be called (provider, key, model and, for openai_compat, a base URL)."""
        provider = self.llm_provider
        if provider == "none" or not self.llm_key or not self.llm_model:
            return False
        if provider == "openai_compat" and not self.llm_base_url:
            return False
        return True

    @property
    def llm_replay_identity(self) -> tuple[str, str] | None:
        """(provider, model) whose recorded turns may be replayed without a key; None when AI is switched off."""
        if self.llm_provider_setting == "none":
            return None
        if self.llm_provider != "none":
            return self.llm_provider, self.llm_model
        return DEFAULT_FREE_PROVIDER, self.llm_model_setting or LLM_DEFAULT_MODELS[DEFAULT_FREE_PROVIDER]


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --- deploy secrets check (builder B; called from app.main's lifespan) ---------------------------------------

DEV_SECRET_DEFAULT = "dev-only-change-me"
MIN_SECRET_LEN = 16
MIN_MCP_TOKEN_LEN = 24
# Set by the hosting platform (Render, Azure App Service, Cloud Run): a platform run is a deployment even when
# PUBLIC_BASE_URL was left at its localhost default.
_HOST_ENV_MARKERS = ("RENDER", "WEBSITE_SITE_NAME", "K_SERVICE")


def on_hosting_platform() -> bool:
    import os

    return any(os.environ.get(name) for name in _HOST_ENV_MARKERS)


def is_deployed(settings: Settings) -> bool:
    """A public https PUBLIC_BASE_URL means a real deployment; http://localhost is local development."""
    return settings.public_base_url.strip().lower().startswith("https://")


def weak_secrets(settings: Settings) -> list[str]:
    """Names of signing secrets still at the public default or shorter than MIN_SECRET_LEN."""
    weak = []
    for name, value in (
        ("MANDATE_HMAC_SECRET", settings.mandate_hmac_secret),
        ("SESSION_SECRET", settings.session_secret),
    ):
        if not value or value == DEV_SECRET_DEFAULT or len(value) < MIN_SECRET_LEN:
            weak.append(name)
    return weak


def mcp_token_is_short(settings: Settings) -> bool:
    """A set MCP_DEMO_TOKEN shorter than MIN_MCP_TOKEN_LEN is guessable. Warned about, never fatal: refusing to
    boot would take a running demo down over a token the operator may have chosen on purpose."""
    token = (settings.mcp_demo_token or "").strip()
    return bool(token) and len(token) < MIN_MCP_TOKEN_LEN


def check_deploy_secrets(settings: Settings | None = None) -> list[str]:
    """Refuse to start a deployed (https) app with default/short signing secrets; warn locally.

    Anyone who knows the default could forge /join invite tokens and payout mandates. Returns the weak names.
    """
    import logging

    settings = settings or get_settings()
    weak = weak_secrets(settings)
    if weak and (is_deployed(settings) or on_hosting_platform()):
        raise RuntimeError(
            f"refusing to start: {', '.join(weak)} must be set to a random value of at least "
            f"{MIN_SECRET_LEN} characters (24 for MCP_DEMO_TOKEN) when PUBLIC_BASE_URL is https "
            "or the app runs on a hosting platform"
        )
    if mcp_token_is_short(settings):
        logging.getLogger("turnkeeper.config").warning(
            'MCP_DEMO_TOKEN is shorter than %d characters; use a random token (python -c "import secrets; '
            'print(secrets.token_urlsafe(32))")',
            MIN_MCP_TOKEN_LEN,
        )
    if weak:
        logging.getLogger("turnkeeper.config").warning(
            "%s still use the development default; fine locally, never deploy like this", ", ".join(weak)
        )
    return weak
