import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_db
from app.core.redis_client import check_redis_health
from app.api.v1 import api_v1_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("iam-service.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing iam-service...")
    try:
        init_db()
        logger.info("Database migration/DDL verification completed.")
    except Exception as e:
        logger.error(f"Database initialization encountered an error: {e}")

    redis_ok = check_redis_health()
    logger.info(f"Redis connection check: {'HEALTHY' if redis_ok else 'UNAVAILABLE'}")

    yield
    logger.info("iam-service shutting down...")


app = FastAPI(
    title="数智底座用户与角色权限中心 — 伴生服务 (iam-service)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(api_v1_router)


@app.get("/health", tags=["系统健康检查"], summary="健康检查接口")
def health_check():
    return {
        "status": "healthy",
        "service": "iam-service",
        "env": settings.APP_ENV,
        "version": "1.0.0",
    }


# Static and SPA mounting for /ui
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIST_DIR = os.path.join(BASE_DIR, "web_dist")

if not os.path.exists(WEB_DIST_DIR):
    os.makedirs(WEB_DIST_DIR, exist_ok=True)


@app.get("/ui", tags=["嵌入前端"])
@app.get("/ui/{path:path}", tags=["嵌入前端"])
async def serve_ui(path: str = ""):
    target_file = os.path.join(WEB_DIST_DIR, path)
    if path and os.path.isfile(target_file):
        return FileResponse(target_file)
    index_html = os.path.join(WEB_DIST_DIR, "index.html")
    if os.path.isfile(index_html):
        return FileResponse(index_html)
    return JSONResponse(status_code=404, content={"message": "UI assets not found"})
