"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • redis.from_url() chỉ tạo connection pool, KHÔNG kết nối lúc import
    → app vẫn khởi động được khi Redis chưa sẵn sàng (/health vẫn 200)
  • Timeout ngắn để /ready trả 503 nhanh khi Redis chết, không treo request
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: ping Redis, lỗi kết nối → False."""
    try:
        return bool(r.ping())
    except (redis.exceptions.RedisError, OSError):
        return False
