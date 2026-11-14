import time
import uuid
import logging
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import get_redis_client
from app.core.security import (
    encrypt_ticket_payload,
    decrypt_ticket_payload,
    generate_gateway_headers,
)
from app.api.deps import AuthContext, get_current_context
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.jump_ticket import (
    JumpTicketIssueReq,
    JumpTicketIssueResp,
    JumpTicketVerifyReq,
    JumpTicketContextResp,
)

logger = logging.getLogger("iam-service.jump_ticket")

router = APIRouter(prefix="/auth", tags=["跨平台免密JumpTicket"])


@router.post(
    "/jump-ticket/issue",
    response_model=ResponseModel[JumpTicketIssueResp],
    summary="跨平台免密跳转换票签发（60秒时效）",
)
def issue_jump_ticket(
    req: JumpTicketIssueReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    当门户系统需要免密跳转至底层底座（模型工程、算力调度、Agent工程、信通院数据工程）时调用。
    校验用户对目标工作空间的访问权，生成 AES-256 加密的 60s 单次票据，并存入 Redis。
    """
    # 1. 校验空间访问权
    ws = (
        db.query(PermTenantWorkspace)
        .filter(
            PermTenantWorkspace.workspace_id == req.workspace_id,
            PermTenantWorkspace.tenant_id == ctx.tenant_id,
            PermTenantWorkspace.status == 1,
        )
        .first()
    )
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"目标工作空间 {req.workspace_id} 不存在或不可用",
        )

    # 检查空间授权
    binding = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            workspace_id=req.workspace_id,
        )
        .first()
    )
    if not binding and ctx.role_code != "PLATFORM_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"用户无权访问目标工作空间 {req.workspace_id}",
        )

    # 2. 生成票据流水号与载荷
    ticket_id = f"tkt_{uuid.uuid4().hex}"
    payload: Dict[str, Any] = {
        "ticket_id": ticket_id,
        "tenant_id": ctx.tenant_id,
        "user_id": ctx.user_id,
        "workspace_id": req.workspace_id,
        "target_platform": req.target_platform,
        "issued_at": int(time.time()),
        "expires_in": 60,
    }

    # 3. AES-256 密文加密
    encrypted_ticket = encrypt_ticket_payload(payload)

    # 4. 写入 Redis（60秒TTL，初始状态 UNCONSUMED）
    redis_key = f"jump:ticket:{ticket_id}"
    try:
        r = get_redis_client()
        r.setex(redis_key, 60, "UNCONSUMED")
    except Exception as e:
        logger.error(f"Failed to save jump ticket to Redis: {e}")
        # 若 Redis 异常仍抛出错误确保单次核销安全
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="安全票据存储异常，请稍后重试",
        )

    logger.info(f"Jump ticket issued: {ticket_id} for user {ctx.user_id} -> {req.target_platform}")

    return ResponseModel(
        data=JumpTicketIssueResp(
            jump_ticket=encrypted_ticket,
            expires_in=60,
        )
    )


@router.post(
    "/jump-ticket/verify",
    response_model=ResponseModel[JumpTicketContextResp],
    summary="子平台单次原子核销票据与网关签名生成",
)
def verify_jump_ticket(
    req: JumpTicketVerifyReq,
    db: Session = Depends(get_db),
):
    """
    子平台后端回调此接口核销票据。
    使用 Redis GETDEL 原子读取并删除（防重放与二次核销，二次调用将直接返回 HTTP 403）。
    核销成功返回完整的租户、主子账号、角色、工作空间、K8s命名空间及 9 大网关签名请求头。
    """
    # 1. 解密 AES-256 票据
    payload = decrypt_ticket_payload(req.jump_ticket)
    if not payload or "ticket_id" not in payload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="票据非法、已被篡改或无法解密",
        )

    ticket_id = payload.get("ticket_id")
    target_platform = payload.get("target_platform")
    tenant_id = payload.get("tenant_id")
    user_id = payload.get("user_id")
    workspace_id = payload.get("workspace_id")

    # 2. Redis 原子核销防重放 (GETDEL)
    redis_key = f"jump:ticket:{ticket_id}"
    try:
        r = get_redis_client()
        # GETDEL: 原子获取并删除
        ticket_state = r.getdel(redis_key)
    except Exception as e:
        logger.error(f"Redis getdel operation failed for ticket {ticket_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="安全核销缓存服务异常",
        )

    if not ticket_state or ticket_state != "UNCONSUMED":
        logger.warning(f"Replay or expired ticket rejected: {ticket_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="票据已失效、已核销或已过期，严禁重复使用",
        )

    # 3. 校验调用方平台一致性
    if target_platform != req.caller_platform:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"目标平台不符: 票据仅限访问 {target_platform}，当前调用方为 {req.caller_platform}",
        )

    # 4. 组装完整上下文
    tenant = db.query(UcTenant).filter_by(tenant_id=tenant_id).first()
    tenant_type = tenant.tenant_type if tenant else "PERSONAL"
    cert_level = tenant.cert_level if tenant else 0

    profile = db.query(UcUserProfile).filter_by(user_id=user_id).first()
    account_type = profile.account_type if profile else "MASTER"
    master_user_id = user_id

    if profile and profile.account_type == "SUB":
        # 物化路径解析出顶层主账号 ID: 如 /usr_master/usr_sub/ -> usr_master
        segments = [s for s in profile.tree_path.split("/") if s]
        if segments:
            master_user_id = segments[0]
        elif profile.parent_user_id:
            master_user_id = profile.parent_user_id

    ws = db.query(PermTenantWorkspace).filter_by(workspace_id=workspace_id).first()
    k8s_ns = ws.k8s_namespace if ws else ""

    bindings = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        .all()
    )
    roles = list({b.role_code for b in bindings}) if bindings else ["TENANT_VIEWER"]
    allowed_modules = list({m for b in bindings for m in (b.allowed_modules or [])})

    primary_role = roles[0] if roles else "TENANT_VIEWER"

    # 5. 生成网关 9 大可信签名请求头
    gw_headers = generate_gateway_headers(
        tenant_id=tenant_id,
        user_id=user_id,
        master_user_id=master_user_id,
        workspace_id=workspace_id,
        role_code=primary_role,
        shadow_account="",
    )

    logger.info(f"Jump ticket {ticket_id} verified and consumed successfully for {user_id}")

    return ResponseModel(
        data=JumpTicketContextResp(
            tenant_id=tenant_id,
            tenant_type=tenant_type,
            cert_level=cert_level,
            user_id=user_id,
            master_user_id=master_user_id,
            account_type=account_type,
            active_workspace_id=workspace_id,
            roles=roles,
            allowed_modules=allowed_modules,
            k8s_namespace=k8s_ns,
            gateway_headers=gw_headers,
        )
    )
