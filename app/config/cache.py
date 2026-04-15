"""
Cache Service - Redis-based caching for performance optimization
Provides caching utilities for ERP data, session info, and API responses.
"""

import json
import logging
from typing import Optional, Any, Union
import aioredis
from cachetools import TTLCache
from app.config.settings import settings

logger = logging.getLogger(__name__)

class CacheService:
    """Redis-based caching service with fallback to in-memory cache."""
    
    def __init__(self):
        self.redis_client: Optional[aioredis.Redis] = None
        self.fallback_cache = TTLCache(maxsize=1000, ttl=settings.CACHE_TTL)
        self.enabled = settings.ENABLE_CACHE
        
    async def initialize(self):
        """Initialize Redis connection."""
        if not self.enabled:
            logger.info("Caching disabled")
            return
            
        try:
            self.redis_client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                retry_on_timeout=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            # Test connection
            await self.redis_client.ping()
            logger.info("Redis cache initialized successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed, using fallback cache: {e}")
            self.redis_client = None
            
    async def close(self):
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()
            
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self.enabled:
            return None
            
        try:
            if self.redis_client:
                value = await self.redis_client.get(key)
                if value:
                    return json.loads(value)
            else:
                # Fallback to in-memory cache
                return self.fallback_cache.get(key)
        except Exception as e:
            logger.warning(f"Cache get error for key {key}: {e}")
        return None
        
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache."""
        if not self.enabled:
            return False
            
        try:
            ttl = ttl or settings.CACHE_TTL
            serialized = json.dumps(value, default=str)
            
            if self.redis_client:
                await self.redis_client.setex(key, ttl, serialized)
            else:
                # Fallback to in-memory cache
                self.fallback_cache[key] = value
            return True
        except Exception as e:
            logger.warning(f"Cache set error for key {key}: {e}")
            return False
            
    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.enabled:
            return False
            
        try:
            if self.redis_client:
                await self.redis_client.delete(key)
            else:
                self.fallback_cache.pop(key, None)
            return True
        except Exception as e:
            logger.warning(f"Cache delete error for key {key}: {e}")
            return False
            
    async def clear_pattern(self, pattern: str) -> int:
        """Clear keys matching pattern."""
        if not self.enabled or not self.redis_client:
            return 0
            
        try:
            keys = await self.redis_client.keys(pattern)
            if keys:
                await self.redis_client.delete(*keys)
                return len(keys)
        except Exception as e:
            logger.warning(f"Cache clear pattern error for {pattern}: {e}")
        return 0
        
    # Convenience methods for specific data types
    async def get_erp_lead(self, lead_id: str) -> Optional[dict]:
        """Get ERP lead data from cache."""
        return await self.get(f"erp:lead:{lead_id}")
        
    async def set_erp_lead(self, lead_id: str, data: dict, ttl: int = 1800) -> bool:
        """Cache ERP lead data (30 min default)."""
        return await self.set(f"erp:lead:{lead_id}", data, ttl)
        
    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get session data from cache."""
        return await self.get(f"session:{session_id}")
        
    async def set_session(self, session_id: str, data: dict, ttl: int = 7200) -> bool:
        """Cache session data (2 hours default)."""
        return await self.set(f"session:{session_id}", data, ttl)
        
    async def invalidate_session(self, session_id: str) -> bool:
        """Invalidate session cache."""
        return await self.delete(f"session:{session_id}")
        
    async def get_token_cache(self, identity: str) -> Optional[str]:
        """Get cached token for identity."""
        return await self.get(f"token:{identity}")
        
    async def set_token_cache(self, identity: str, token: str, ttl: int = 300) -> bool:
        """Cache token for 5 minutes."""
        return await self.set(f"token:{identity}", token, ttl)

# Global cache instance
cache_service = CacheService()
