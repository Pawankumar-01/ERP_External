"""
Configuration / Settings
─────────────────────────
All secrets read from environment variables.
Use .env file for local development — never commit it.

Integration variables added in this version:
  - ERP_WEBHOOK_SECRET   : shared secret for ERPNext → FastAPI webhooks
  - ERP_TIMEOUT          : per-request timeout for ERP API calls
  - ERP_MAX_RETRIES      : max retry attempts on transient failures
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ─── App ─────────────────────────────────────────────────────────────────
    APP_ENV:    str = "development"
    SECRET_KEY: str = "change-me-in-production"

    # ─── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/hospital_automation"
    )

    # ─── LiveKit Cloud ─────────────────────────────────────────────────────────
    LIVEKIT_API_KEY:      str = "your-livekit-api-key"
    LIVEKIT_API_SECRET:   str = "your-livekit-api-secret"
    LIVEKIT_URL:          str = "wss://your-project.livekit.cloud"
    LIVEKIT_WEBHOOK_SECRET: str = "your-livekit-webhook-secret"

    # ─── ERPNext Integration ───────────────────────────────────────────────────
    # Base URL of your Frappe/ERPNext instance (no trailing slash)
    ERPNEXT_BASE_URL:   str = "https://your-site.frappe.cloud"

    # API Key + Secret from ERPNext User → API Access → Generate Keys
    # The user must have System Manager or a custom role with DocType permissions
    ERPNEXT_API_KEY:    str = "your-erpnext-api-key"
    ERPNEXT_API_SECRET: str = "your-erpnext-api-secret"

    # Shared secret used to verify inbound webhooks FROM ERPNext → FastAPI
    # Set the same value in ERPNext Webhook → Secret
    ERP_WEBHOOK_SECRET: str = "your-erp-webhook-secret"

    # HTTP client tuning
    ERP_TIMEOUT:     int = 15    # seconds per request
    ERP_MAX_RETRIES: int = 3     # attempts before giving up

    #Whatsapp
    WHATSAPP_TOKEN: str
    WHATSAPP_PHONE_ID: int
    FRONTEND_BASE_URL: str = "http://localhost:8001"

    # ─── CORS ─────────────────────────────────────────────────────────────────
    # Production CORS origins - never use wildcard in production
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:5500",  # For local development
        "https://your-production-domain.com",  # Replace with actual domain
    ]
    
    # Additional security settings
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]

    # ─── Caching ───────────────────────────────────────────────────────────────────
    # Redis configuration for caching
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 3600  # 1 hour default TTL
    ENABLE_CACHE: bool = True

    # ─── Orientation Business Rules ───────────────────────────────────────────
    # Minimum fraction of session duration a participant must watch
    ORIENTATION_COMPLETION_THRESHOLD: float = 0.70   # 70 %

    # ─── AI Casesheet ─────────────────────────────────────────────────────────────
    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    LLM_MODEL: str = "google/gemma-4-31b-it:free"



settings = Settings()