
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    APP_ENV:    str = "development"
    SECRET_KEY: str = "change-me-in-production"

    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/hospital_automation"
    )

    LIVEKIT_API_KEY:      str = "your-livekit-api-key"
    LIVEKIT_API_SECRET:   str = "your-livekit-api-secret"
    LIVEKIT_URL:          str = "wss://your-project.livekit.cloud"
    LIVEKIT_WEBHOOK_SECRET: str = "your-livekit-webhook-secret"

    ERPNEXT_BASE_URL:   str = "https://your-site.frappe.cloud"

    ERPNEXT_API_KEY:    str = "your-erpnext-api-key"
    ERPNEXT_API_SECRET: str = "your-erpnext-api-secret"

    ERP_WEBHOOK_SECRET: str = "your-erp-webhook-secret"

    ERP_TIMEOUT:     int = 15
    ERP_MAX_RETRIES: int = 3

    WHATSAPP_TOKEN: str
    WHATSAPP_PHONE_ID: int
    WHATSAPP_VERIFY_TOKEN: str = "sgp_whatsapp_verify_token_2026"
    FRONTEND_BASE_URL: str = "http://localhost:8001"

    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:5500",
        "https://your-production-domain.com",
    ]
    
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 3600
    ENABLE_CACHE: bool = True

    ORIENTATION_COMPLETION_THRESHOLD: float = 0.70

    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    LLM_MODEL: str = "google/gemma-4-31b-it:free"



settings = Settings()