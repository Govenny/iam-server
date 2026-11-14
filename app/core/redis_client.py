import logging
import redis
from app.core.config import settings

logger = logging.getLogger("iam-service.redis")

redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    max_connections=50,
)


def get_redis_client() -> redis.Redis:
    return redis.Redis(connection_pool=redis_pool)


def check_redis_health() -> bool:
    try:
        r = get_redis_client()
        return bool(r.ping())
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        return False
