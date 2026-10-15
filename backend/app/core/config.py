"""Application configuration loaded from environment variables.

All settings are read once at import time via ``get_settings`` (cached).
Secrets are never logged. See ``.env.example`` for the documented surface.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the AirDesk backend."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "AirDesk"
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    secret_key: str = Field(
        default="dev-only-insecure-secret-change-me",
        description="HMAC key for JWT signing. MUST be overridden in production.",
    )
    access_token_expire_minutes: int = 60 * 12
    refresh_token_expire_days: int = 7
    # CSV in env (CORS_ORIGINS=a,b). Parsed via `cors_origins_list`.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://airdesk:airdesk@localhost:5432/airdesk",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- Redis / cache -----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM (LiteLLM) -----------------------------------------------------
    # LiteLLM is the single gateway for every model call. The model string uses
    # LiteLLM's "provider/model" convention. The prefix selects the wire protocol,
    # NOT necessarily the vendor: a Claude-compatible gateway may serve models
    # from other families, e.g. "openai/big-pickle" against a Claude-compatible
    # proxy. Point LLM_API_BASE at that proxy and keep the prefix it speaks.
    llm_model: str = "openai/gpt-4o-mini"
    # CSV in env (LLM_FALLBACK_MODELS=a,b). Parsed via `llm_fallback_models_list`.
    llm_fallback_models: str = ""
    llm_api_base: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_temperature: float = 0.2
    # Optional routing profile consumed by the LiteLLM proxy for budgets/limits.
    litellm_router_settings: dict | None = None

    # --- LLM model default --------------------------------------------------
    # LLM_MODEL is OPTIONAL. Leave it blank (or set to "auto") and the gateway
    # auto-discovers the model list from {LLM_API_BASE}/v1/models at call time:
    # the first id is the primary model, the rest are an ordered fallback chain.
    # Operators only ever need LLM_API_KEY + LLM_API_BASE to turn AI on; no
    # model string is required. To pin a model (bypassing discovery), set
    # LLM_MODEL to a literal "provider/model" string. LiteLLM requires a
    # non-empty model on every call, so a blank value collapses to the "auto"
    # sentinel which the gateway resolves via discovery (with a hard-coded
    # last-resort fallback so the app still boots if the gateway is unreachable).
    @field_validator("llm_model")
    @classmethod
    def _coerce_llm_model(cls, v: str) -> str:
        return v.strip() or "auto"

    # --- Flight providers --------------------------------------------------
    # CSV in env (FLIGHT_PROVIDERS=mock,amadeus). The mock provider always works offline.
    flight_providers: str = "opensky,mock,amadeus"
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_base_url: str = "https://test.api.amadeus.com"
    duffel_access_token: str | None = None
    duffel_base_url: str = "https://api.duffel.com"
    duffel_version: str = "v2"
    provider_timeout_seconds: int = 30
    provider_max_retries: int = 3

    # --- Messaging integrations (graceful when unset) ----------------------
    zalo_oa_access_token: str | None = None
    zalo_app_id: str | None = None
    zalo_app_secret: str | None = None
    facebook_page_access_token: str | None = None
    facebook_verify_token: str | None = None
    # Meta app secret, used to verify the ``X-Hub-Signature-256`` header.
    facebook_app_secret: str | None = None
    # Inbound webhook payloads must carry a valid HMAC signature. Turning this
    # off is only appropriate for local tunnels where the signature cannot be
    # produced; production must keep it on.
    webhook_require_signature: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@airdesk.local"
    # Inbound mail (IMAP) — the whole pipeline is a no-op when imap_host is unset.
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    # Mailbox to poll; usually "INBOX".
    imap_mailbox: str = "INBOX"
    # Safety valve: never fetch more than this many UNSEEN messages per poll.
    imap_max_messages: int = 25
    # Off by default: an operator opts in before a scheduled poll runs.
    email_poll_enabled: bool = False
    # When False, inbound mail is stored + classified but the agent stays quiet.
    email_auto_reply_enabled: bool = True

    # --- Documents (upload + extraction) -----------------------------------
    # Hard cap on one uploaded file; enforced before a row is created.
    document_max_bytes: int = 10_485_760
    # CSV in env (DOCUMENT_ALLOWED_TYPES=a,b). Parsed via `document_allowed_types_list`.
    document_allowed_types: str = "application/pdf,text/plain,text/csv,image/png,image/jpeg"

    # --- Guardrails / human-in-the-loop ------------------------------------
    # A booking above this total (VND) always requires human approval.
    booking_auto_approve_limit_vnd: int = 20_000_000
    # Same ceiling for cargo: declared value above it forces an approval task.
    # Higher than bookings because consignments legitimately carry more value.
    shipment_auto_approve_limit_vnd: int = 50_000_000
    # Every agent action over this confidence is auto-executed, else escalated.
    agent_confidence_threshold: float = 0.75

    # --- Rate limiting -----------------------------------------------------
    rate_limit_default: str = "120/minute"

    # --- Parsed list helpers (env vars are CSV strings) --------------------
    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins parsed from the comma-separated env value."""
        return self._csv(self.cors_origins)

    @property
    def flight_providers_list(self) -> list[str]:
        """Configured flight provider names, in priority order."""
        return self._csv(self.flight_providers)

    @property
    def llm_fallback_models_list(self) -> list[str]:
        """Fallback model chain for LiteLLM."""
        return self._csv(self.llm_fallback_models)

    @property
    def document_allowed_types_list(self) -> list[str]:
        """Accepted upload content types, parsed from the comma-separated env value."""
        return self._csv(self.document_allowed_types)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_runtime(self) -> list[str]:
        """Return a list of production-readiness warnings (never raises)."""
        warnings: list[str] = []
        if self.is_production and "insecure-secret" in self.secret_key:
            warnings.append("SECRET_KEY is the insecure default in production.")
        if self.is_production and self.debug:
            warnings.append("DEBUG must be disabled in production.")
        if self.is_production and any(
            o.startswith("http://localhost") for o in self.cors_origins_list
        ):
            warnings.append("CORS_ORIGINS still contains localhost in production.")
        return warnings


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
