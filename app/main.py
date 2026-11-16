import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_db
from app.core.redis_client import check_redis_health
from app.schemas.common import (
    generate_timestamp_str,
    generate_trans_id,
    SysHeadResp,
    SysAttached,
)
from app.api.v1 import api_v1_router
from app.api.v1.portal_sync import query_user_info

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
    description="符合《国家工业中试基地与数智中台 统一接口规范》的多租户权限伴生中心",
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


@app.middleware("http")
async def unified_spec_middleware(request: Request, call_next):
    """
    国家工业中试基地与数智中台 统一接口规范 协议适配中间件:
    1. 接收请求时：若报文包含 SYS_HEAD / SYS_BODY，提取系统头并解包业务参数（支持 _XXX_REQ 嵌套及大小写参数兼容）
    2. 返回响应时：确保响应头 SYS_HEAD 严格对齐并回显请求方的 APP_ID 与 TRANS_ID 序列号
    """
    content_type = request.headers.get("content-type", "")
    req_sys_head = {}

    if "application/json" in content_type:
        try:
            body = await request.body()
            if body:
                payload = json.loads(body.decode("utf-8"))
                if isinstance(payload, dict) and "SYS_HEAD" in payload:
                    req_sys_head = payload.get("SYS_HEAD") or {}
                    request.state.sys_head = req_sys_head

                    sys_body = payload.get("SYS_BODY", {})
                    inner_payload = None
                    if isinstance(sys_body, dict):
                        for k, v in sys_body.items():
                            if k.startswith("_") and k.endswith("_REQ") and isinstance(v, dict):
                                inner_payload = v
                                break
                        if inner_payload is None:
                            inner_payload = sys_body
                    else:
                        inner_payload = {}

                    # 兼容大小写字段入参
                    norm_dict = {}
                    for k, v in inner_payload.items():
                        norm_dict[k] = v
                        norm_dict[k.lower()] = v

                    new_body = json.dumps(norm_dict).encode("utf-8")
                    request._body = new_body

                    async def receive():
                        return {"type": "http.request", "body": new_body, "more_body": False}

                    request._receive = receive
        except Exception as e:
            logger.warning(f"Failed to unwrap unified request body: {e}")

    app_id = req_sys_head.get("APP_ID") or request.headers.get("X-APP-ID") or "APP_SZ_PILOT_001"
    trans_id = req_sys_head.get("TRANS_ID") or request.headers.get("X-TRANS-ID")
    request.state.app_id = app_id
    request.state.trans_id = trans_id

    response = await call_next(request)

    # 针对 JSON 响应回显请求系统参数
    resp_content_type = response.headers.get("content-type", "")
    if "application/json" in resp_content_type:
        try:
            res_body = b""
            async for chunk in response.body_iterator:
                res_body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

            if res_body:
                resp_json = json.loads(res_body.decode("utf-8"))
                if isinstance(resp_json, dict) and "SYS_HEAD" in resp_json:
                    head = resp_json["SYS_HEAD"]
                    if app_id:
                        head["APP_ID"] = app_id
                    if trans_id:
                        head["TRANS_ID"] = trans_id
                    res_body = json.dumps(resp_json, ensure_ascii=False).encode("utf-8")

            headers = dict(response.headers)
            headers["content-length"] = str(len(res_body))
            return Response(
                content=res_body,
                status_code=response.status_code,
                headers=headers,
                media_type="application/json",
            )
        except Exception as e:
            logger.warning(f"Failed to echo SYS_HEAD in response: {e}")

    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    app_id = getattr(request.state, "app_id", "APP_SZ_PILOT_001")
    trans_id = getattr(request.state, "trans_id", None) or generate_trans_id()
    resp_code = "A99997" if exc.status_code < 500 else "A99999"
    if exc.status_code == 404:
        resp_code = "C10000"
    elif exc.status_code in (401, 403):
        resp_code = "A00002"

    error_payload = {
        "SYS_HEAD": {
            "APP_ID": app_id,
            "TIMESTAMP": generate_timestamp_str(),
            "TRANS_ID": trans_id,
            "RESP_CODE": resp_code,
            "RESP_DESC": str(exc.detail),
            "RESERVED": [],
        },
        "SYS_BODY": {
            "BIZ_CODE": str(exc.status_code),
            "BIZ_MSG": str(exc.detail),
        },
        "SYS_ATTACHED": {
            "MEDIA_INFO": "",
        },
        "code": exc.status_code,
        "message": str(exc.detail),
        "detail": exc.detail,
    }
    return JSONResponse(status_code=exc.status_code, content=error_payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    app_id = getattr(request.state, "app_id", "APP_SZ_PILOT_001")
    trans_id = getattr(request.state, "trans_id", None) or generate_trans_id()
    error_payload = {
        "SYS_HEAD": {
            "APP_ID": app_id,
            "TIMESTAMP": generate_timestamp_str(),
            "TRANS_ID": trans_id,
            "RESP_CODE": "A99997",
            "RESP_DESC": "请求报文校验出错",
            "RESERVED": [],
        },
        "SYS_BODY": {
            "BIZ_CODE": "4220",
            "BIZ_MSG": "请求参数校验失败",
            "errors": exc.errors(),
        },
        "SYS_ATTACHED": {
            "MEDIA_INFO": "",
        },
        "code": 422,
        "message": "请求参数校验失败",
        "detail": exc.errors(),
    }
    return JSONResponse(status_code=422, content=error_payload)


# Include API Routers
app.include_router(api_v1_router)

# 挂载规范 2.2.3 具象示例 REST URL: /api/customer/cuseser/v1
app.add_api_route(
    "/api/customer/cuseser/v1",
    query_user_info,
    methods=["POST", "GET"],
    tags=["国家中试基地数智中台能力开放"],
    summary="数智中台标准接口 2.2.3 用户信息查询",
)


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
