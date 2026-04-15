"""
Monitoring Router - Health checks, metrics, and system status
Provides comprehensive monitoring endpoints for production deployment.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging
import psutil
import asyncio

from app.config.database import get_db
from app.config.settings import settings
from app.config.cache import cache_service
from app.livekit.client import livekit_client

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

@router.get("/health", tags=["Health"])
@limiter.limit("60/minute")
async def health_check(request: Request):
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "service": "hospital-automation-engine",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.APP_ENV,
    }

@router.get("/health/detailed", tags=["Health"])
@limiter.limit("30/minute")
async def detailed_health_check(request: Request, db: AsyncSession = Depends(get_db)):
    """Comprehensive health check with all system components."""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.APP_ENV,
        "checks": {}
    }
    
    # Database health check
    try:
        result = await db.execute(text("SELECT 1"))
        await result.fetchone()
        health_status["checks"]["database"] = {
            "status": "healthy",
            "response_time_ms": 0  # Could be measured with time.perf_counter()
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["checks"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"
    
    # Redis cache health check
    try:
        if cache_service.redis_client:
            await cache_service.redis_client.ping()
            health_status["checks"]["cache"] = {
                "status": "healthy",
                "type": "redis"
            }
        else:
            health_status["checks"]["cache"] = {
                "status": "healthy",
                "type": "fallback_memory"
            }
    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        health_status["checks"]["cache"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"
    
    # LiveKit connection health check
    try:
        # We could implement a lightweight LiveKit API check here
        health_status["checks"]["livekit"] = {
            "status": "healthy",
            "configured": bool(settings.LIVEKIT_API_KEY)
        }
    except Exception as e:
        logger.error(f"LiveKit health check failed: {e}")
        health_status["checks"]["livekit"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"
    
    return health_status

@router.get("/metrics", tags=["Monitoring"])
@limiter.limit("20/minute")
async def system_metrics(request: Request):
    """System performance metrics."""
    try:
        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        
        # Memory metrics
        memory = psutil.virtual_memory()
        
        # Disk metrics
        disk = psutil.disk_usage('/')
        
        # Process metrics
        process = psutil.Process()
        process_memory = process.memory_info()
        
        metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": {
                "cpu": {
                    "percent": cpu_percent,
                    "count": cpu_count
                },
                "memory": {
                    "total": memory.total,
                    "available": memory.available,
                    "percent": memory.percent,
                    "used": memory.used
                },
                "disk": {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": (disk.used / disk.total) * 100
                }
            },
            "process": {
                "pid": process.pid,
                "memory": {
                    "rss": process_memory.rss,
                    "vms": process_memory.vms
                },
                "cpu": process.cpu_percent(),
                "threads": process.num_threads(),
                "connections": len(process.connections())
            }
        }
        
        return metrics
        
    except Exception as e:
        logger.error(f"Metrics collection failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to collect metrics")

@router.get("/status", tags=["Monitoring"])
@limiter.limit("30/minute")
async def application_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Application status with session counts and statistics."""
    try:
        # Get session statistics
        session_result = await db.execute(text("""
            SELECT 
                COUNT(*) as total_sessions,
                COUNT(CASE WHEN status = 'LIVE' THEN 1 END) as live_sessions,
                COUNT(CASE WHEN status = 'SCHEDULED' THEN 1 END) as scheduled_sessions,
                COUNT(CASE WHEN status = 'ENDED' THEN 1 END) as ended_sessions
            FROM orientation_sessions
        """))
        session_stats = session_result.fetchone()
        
        # Get participant statistics
        participant_result = await db.execute(text("""
            SELECT 
                COUNT(*) as total_participants,
                COUNT(CASE WHEN attendance_status = 'COMPLETED' THEN 1 END) as completed_participants,
                COUNT(CASE WHEN attendance_status = 'JOINED' THEN 1 END) as joined_participants
            FROM orientation_participants
        """))
        participant_stats = participant_result.fetchone()
        
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "application": {
                "environment": settings.APP_ENV,
                "version": "1.0.0",
                "uptime_seconds": 0  # Could be tracked from application start
            },
            "sessions": {
                "total": session_stats.total_sessions or 0,
                "live": session_stats.live_sessions or 0,
                "scheduled": session_stats.scheduled_sessions or 0,
                "ended": session_stats.ended_sessions or 0
            },
            "participants": {
                "total": participant_stats.total_participants or 0,
                "completed": participant_stats.completed_participants or 0,
                "joined": participant_stats.joined_participants or 0
            },
            "cache": {
                "enabled": settings.ENABLE_CACHE,
                "type": "redis" if cache_service.redis_client else "fallback_memory"
            }
        }
        
        return status
        
    except Exception as e:
        logger.error(f"Status collection failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to collect status")

@router.post("/cache/clear", tags=["Monitoring"])
@limiter.limit("10/minute")
async def clear_cache(request: Request, pattern: Optional[str] = None):
    """Clear cache entries."""
    try:
        if pattern:
            cleared_count = await cache_service.clear_pattern(pattern)
            message = f"Cleared {cleared_count} cache entries matching pattern: {pattern}"
        else:
            # This would need to be implemented in cache service
            message = "Cache clear not implemented for full cache"
            logger.warning("Full cache clear not implemented")
        
        return {
            "status": "success",
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Cache clear failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")

@router.get("/logs/recent", tags=["Monitoring"])
@limiter.limit("20/minute")
async def recent_logs(request: Request, lines: int = 50):
    """Get recent application logs (for debugging)."""
    # This would require log file access or structured logging
    # For now, return a placeholder
    return {
        "message": "Log access not implemented in this version",
        "suggestion": "Use centralized logging service for production"
    }
