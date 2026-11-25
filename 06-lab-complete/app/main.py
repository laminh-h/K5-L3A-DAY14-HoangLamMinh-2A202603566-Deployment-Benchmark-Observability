"""
Production AI Agent — Part 6 Final Project

  • GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  • GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không (hoặc đang tắt)
  • POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → mock LLM
  • Log: mỗi dòng là 1 JSON object
  • Graceful shutdown: uvicorn xử lý SIGTERM (ngừng nhận request mới, chờ request
    đang chạy xong, chạy lifespan shutdown rồi thoát exit code 0). App chỉ móc thêm
    vào handler đó để /ready trả 503 ngay khi bắt đầu tắt, rồi gọi lại handler cũ.

Chạy: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
"""
import json
import logging
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask

APP_VERSION = "1.0.0"


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log(level: int, event: str, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown
# ─────────────────────────────────────────────────────────
_state = {"shutting_down": False}


def _hook_sigterm() -> None:
    """
    Gọi trong lifespan startup — lúc này uvicorn đã cài handler SIGTERM của nó.
    Handler mới chỉ đánh dấu "đang tắt" (để /ready trả 503, load balancer ngừng gửi
    request mới) rồi GỌI LẠI handler của uvicorn để uvicorn tắt êm như bình thường.
    """
    previous = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        _state["shutting_down"] = True
        log(logging.INFO, "sigterm_received", detail="draining in-flight requests (graceful shutdown)")
        if callable(previous):
            previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        pass  # không ở main thread (vd test client) → để uvicorn tự xử lý


@asynccontextmanager
async def lifespan(app: FastAPI):
    _hook_sigterm()
    log(logging.INFO, "startup", version=APP_VERSION, environment=settings.environment,
        port=settings.port, rate_limit_per_minute=settings.rate_limit_per_minute,
        daily_budget_usd=settings.daily_budget_usd, monthly_budget_usd=settings.monthly_budget_usd,
        redis_ok=redis_healthy())
    yield
    # Tới đây uvicorn đã chờ xong các request đang chạy
    log(logging.INFO, "shutdown", detail="graceful shutdown complete — closing Redis connections")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(title="Production AI Agent", version=APP_VERSION, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware: request id + access log JSON
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log(logging.INFO, "request", request_id=request_id, method=request.method,
            path=request.url.path, status=status, ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = []
    for e in exc.errors():
        if e.get("type") == "json_invalid":
            errors.append({"field": "body", "message": "Request body is not valid JSON"})
        else:
            field = ".".join(str(p) for p in e["loc"] if p != "body") or "body"
            errors.append({"field": field, "message": e["msg"]})
    return JSONResponse(status_code=422, content={"error": "Invalid request body", "details": errors})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    # detail có thể là chuỗi hoặc dict (vd 429/402 kèm số liệu) → luôn trả {"error": ..., ...}
    content = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error(request: Request, exc: redis.exceptions.RedisError):
    log(logging.ERROR, "redis_unavailable", path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"error": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": APP_VERSION, "endpoints": ["/health", "/ready", "/ask"]}


@app.get("/health")
def health():
    """Liveness — luôn 200 khi process còn chạy, kể cả khi Redis chết."""
    return {"status": "ok", "version": APP_VERSION, "environment": settings.environment,
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def ready():
    """Readiness — 503 khi Redis không kết nối được hoặc đang shutdown."""
    if _state["shutting_down"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # `def` (không async): FastAPI chạy trong threadpool → LLM/Redis chặn không làm nghẽn event loop
    rate = check_rate_limit(api_key)                    # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                                      # 402 khi hết DAILY/MONTHLY budget

    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log(logging.INFO, "llm_call", user_id=body.user_id, question_chars=len(body.question),
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8), "rate_limit_remaining": rate["remaining"]},
    }
