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

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):

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

    # ─── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "*",
    ]

    # ─── Orientation Business Rules ───────────────────────────────────────────
    # Minimum fraction of session duration a participant must watch
    ORIENTATION_COMPLETION_THRESHOLD: float = 0.70   # 70 %

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()