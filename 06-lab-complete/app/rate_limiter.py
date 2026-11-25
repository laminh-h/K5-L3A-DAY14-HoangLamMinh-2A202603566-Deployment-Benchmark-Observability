"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp) để mọi worker / instance
    dùng chung và vẫn còn sau khi restart agent

Các bước ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE chạy trong một Lua script
nên là atomic: 2 worker gọi cùng lúc không thể cùng lọt qua giới hạn.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

# KEYS[1] = key của window; ARGV = now, window, limit, member
# Trả về {được phép (1/0), số request trong window, timestamp request cũ nhất}
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, count, oldest[2]}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return {1, count + 1, '0'}
"""
_sliding_window = r.register_script(_SLIDING_WINDOW_LUA)


def _bucket_key(api_key: str) -> str:
    # Không lưu API key gốc trong Redis — chỉ lưu hash
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:24]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> dict:
    """Raise 429 nếu `key` đã dùng hết RATE_LIMIT_PER_MINUTE request trong 60s gần nhất."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    allowed, count, oldest = _sliding_window(
        keys=[_bucket_key(key)],
        args=[now, WINDOW_SECONDS, limit, f"{now:.6f}-{uuid.uuid4().hex}"],
    )
    if not int(allowed):
        retry_after = max(1, math.ceil(float(oldest) + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit_per_minute": limit,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    return {"limit": limit, "remaining": max(0, limit - int(count))}
