"""
Hospital Automation Engine - Main Application Entry Point
FastAPI-based external automation layer for ERPNext Hospital ERP.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging

from app.config.settings import settings
from app.leads.router import router as leads_router
from app.orientation.router import router as orientation_router
from app.livekit.router import router as livekit_router
from app.erp_bridge.router import router as erp_router
from app.events.router import router as events_router
from app.assessment.router import router as assessment_router
from app.appointment.router import router as appointment_router
from app.events.logger import event_logger
from app.whatsapp.router import router as whatsapp_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    logger.info("🏥 Hospital Automation Engine starting up...")
    await event_logger.initialize()
    logger.info("✅ Event logger initialized")
    yield
    logger.info("🔴 Hospital Automation Engine shutting down...")
    await event_logger.shutdown()


app = FastAPI(
    title="Hospital Automation Engine",
    description="External automation layer for Ayurvedic + Integrative Medicine ERP",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows Flutter Web (Chrome) to connect locally
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.casesheet.router import router as casesheet_router

# ─── Routers ─────────────────────────────────────────────────────────────────
app.include_router(leads_router,       prefix="/api/v1/leads",       tags=["Leads"])
app.include_router(orientation_router, prefix="/api/v1/orientation",  tags=["Orientation"])
app.include_router(livekit_router,     prefix="/api/v1/livekit",      tags=["LiveKit"])
app.include_router(erp_router,         prefix="/api/v1/erp",          tags=["ERP Bridge"])
app.include_router(events_router,      prefix="/api/v1/events",       tags=["Events"])
app.include_router(assessment_router,  prefix="/api/v1/assessment",   tags=["Assessment"])
app.include_router(appointment_router, prefix="/api/v1/appointments", tags=["Appointments"])
app.include_router(casesheet_router,   prefix="/api/v1/casesheet",    tags=["Case Sheet V2"])
app.include_router(whatsapp_router, prefix="/api/v1/whatsapp", tags=["WhatsApp"])

import os
os.makedirs("uploads/lab_reports", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ─── Static files (frontend) ─────────────────────────────────────────────────
app.mount("/meet", StaticFiles(directory="frontend/orientation_meet", html=True), name="meet")


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "hospital-automation-engine",
        "version": "1.0.0",
    }
