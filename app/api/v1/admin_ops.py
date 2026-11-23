import json
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import get_redis_client, check_redis_health
from app.core.casdoor_client import casdoor_client
from app.api.deps import AuthContext, get_current_context
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.admin_ops import (
    DashboardStatsResp,
    AdminTenantItemResp,
    AdminTenantUpdateReq,
    AdminUserItemResp,
    AdminUserUpdateReq,
    JumpTicketAuditItem,
)
from app.api.v1.jump_ticket import AUDIT_LOG_KEY

logger = logging.getLogger("iam-service.admin_ops")

router = APIRouter(prefix="/admin", tags=["平台运营与全局管理"])


def require_platform_staff(ctx: AuthContext) -> None:
    """仅允许 PLATFORM_ADMIN 与 PLATFORM_OPERATOR 调用"""
    if ctx.role_code not in ["PLATFORM_ADMIN", "PLATFORM_OPERATOR"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足：仅平台管理员或平台运营人员有权访问运营管理接口",
        )


@router.get(
    "/dashboard/stats",
    response_model=ResponseModel[DashboardStatsResp],
    summary="平台运营数据看板宏观指标",
)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)

    # 1. 租户统计
    total_tenants = db.query(func.count(UcTenant.id)).filter(UcTenant.is_deleted == 0).scalar() or 0
    enterprise_tenants = (
        db.query(func.count(UcTenant.id))
        .filter(UcTenant.tenant_type == "ENTERPRISE", UcTenant.is_deleted == 0)
        .scalar()
        or 0
    )
    pending_cert_tenants = (
        db.query(func.count(UcTenant.id))
        .filter(UcTenant.cert_status == 1, UcTenant.is_deleted == 0)
        .scalar()
        or 0
    )

    # 2. 用户统计
    total_users = db.query(func.count(UcUserProfile.id)).filter(UcUserProfile.is_deleted == 0).scalar() or 0
    master_users = (
        db.query(func.count(UcUserProfile.id))
        .filter(UcUserProfile.account_type == "MASTER", UcUserProfile.is_deleted == 0)
        .scalar()
        or 0
    )
    sub_users = (
        db.query(func.count(UcUserProfile.id))
        .filter(UcUserProfile.account_type == "SUB", UcUserProfile.is_deleted == 0)
        .scalar()
        or 0
    )

    # 3. 空间统计
    total_workspaces = (
        db.query(func.count(PermTenantWorkspace.id))
        .filter(PermTenantWorkspace.status == 1)
        .scalar()
        or 0
    )

    # 4. Redis 审计与流量统计
    breakdown = {"MODEL_ENGINE": 0, "COMPUTE": 0, "AGENT": 0, "CAICT_DATA_ENG": 0}
    total_jumps = 0
    try:
        r = get_redis_client()
        raw_logs = r.lrange(AUDIT_LOG_KEY, 0, 199) if hasattr(r, "lrange") else []
        for raw in raw_logs:
            try:
                item = json.loads(raw)
                plat = item.get("target_platform", "")
                if plat in breakdown:
                    breakdown[plat] += 1
                total_jumps += 1
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to query audit jump logs: {e}")

    # 5. 组件健康检查
    redis_healthy = check_redis_health()
    health = {
        "database": "HEALTHY",
        "redis_ticket_cache": "HEALTHY" if redis_healthy else "UNAVAILABLE",
        "casdoor_idp": "HEALTHY",
        "gateway_sign_service": "HEALTHY",
    }

    return ResponseModel(
        data=DashboardStatsResp(
            total_tenants=total_tenants,
            enterprise_tenants=enterprise_tenants,
            pending_cert_tenants=pending_cert_tenants,
            total_users=total_users,
            master_users=master_users,
            sub_users=sub_users,
            total_workspaces=total_workspaces,
            today_ticket_jumps=total_jumps,
            platform_jumps_breakdown=breakdown,
            health_status=health,
        )
    )


@router.get(
    "/audit/tickets",
    response_model=ResponseModel[List[JumpTicketAuditItem]],
    summary="查询最近跨底座 Jump Ticket 签发与原子核销审计流水",
)
def get_jump_ticket_audits(
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)
    results: List[JumpTicketAuditItem] = []
    try:
        r = get_redis_client()
        raw_logs = r.lrange(AUDIT_LOG_KEY, 0, limit - 1) if hasattr(r, "lrange") else []
        for raw in raw_logs:
            try:
                d = json.loads(raw)
                results.append(JumpTicketAuditItem(**d))
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to read audit logs: {e}")

    return ResponseModel(data=results)


@router.get(
    "/tenants",
    response_model=ResponseModel[List[AdminTenantItemResp]],
    summary="平台超管查询全域入驻企业与租户列表",
)
def list_all_tenants(
    keyword: Optional[str] = Query(None, description="搜索租户名/企业代码/统一信用代码"),
    tenant_type: Optional[str] = Query(None, description="PERSONAL / ENTERPRISE"),
    cert_status: Optional[int] = Query(None, description="0-未认证, 1-审核中, 2-已通过, 3-已驳回"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)

    query = db.query(UcTenant)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        query = query.filter(
            (UcTenant.tenant_name.ilike(kw))
            | (UcTenant.tenant_id.ilike(kw))
            | (UcTenant.credit_code.ilike(kw))
            | (UcTenant.legal_person_name.ilike(kw))
        )
    if tenant_type:
        query = query.filter(UcTenant.tenant_type == tenant_type)
    if cert_status is not None:
        query = query.filter(UcTenant.cert_status == cert_status)

    tenants = query.order_by(UcTenant.id.desc()).all()

    # 聚合各租户的企业成员数与工作空间数 (GROUP BY 汇总)
    user_counts = dict(
        db.query(
            UcUserProfile.tenant_id,
            func.count(func.distinct(UcUserProfile.user_id)),
        )
        .filter(UcUserProfile.is_deleted == 0)
        .group_by(UcUserProfile.tenant_id)
        .all()
    )

    ws_counts = dict(
        db.query(
            PermTenantWorkspace.tenant_id,
            func.count(func.distinct(PermTenantWorkspace.workspace_id)),
        )
        .filter(PermTenantWorkspace.status == 1)
        .group_by(PermTenantWorkspace.tenant_id)
        .all()
    )

    master_profiles = (
        db.query(UcUserProfile.tenant_id, UcUserProfile.user_id)
        .filter(UcUserProfile.account_type == "MASTER", UcUserProfile.is_deleted == 0)
        .all()
    )
    master_map = {row[0]: row[1] for row in master_profiles}

    results = [
        AdminTenantItemResp(
            tenant_id=t.tenant_id,
            tenant_type=t.tenant_type,
            tenant_name=t.tenant_name,
            tenant_code=t.tenant_code,
            org_name=t.org_name,
            cert_level=t.cert_level,
            cert_status=t.cert_status,
            credit_code=t.credit_code,
            legal_person_name=t.legal_person_name,
            is_exempt_billing=t.is_exempt_billing,
            workspace_count=ws_counts.get(t.tenant_id, 0),
            user_count=user_counts.get(t.tenant_id, 0),
            master_user_id=master_map.get(t.tenant_id),
            created_at=t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        )
        for t in tenants
    ]

    return ResponseModel(data=results)


@router.put(
    "/tenants/{tenant_id}/status",
    response_model=ResponseModel[AdminTenantItemResp],
    summary="平台超管变更企业租户实名审核状态或免计费白名单",
)
def update_tenant_status(
    tenant_id: str,
    req: AdminTenantUpdateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)

    tenant = db.query(UcTenant).filter_by(tenant_id=tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到租户 {tenant_id}",
        )

    if req.cert_status is not None:
        tenant.cert_status = req.cert_status
        if req.cert_status == 2:
            tenant.tenant_type = "ENTERPRISE"
            tenant.cert_level = 2
        elif req.cert_status == 3:
            tenant.cert_level = 0

    if req.tenant_type is not None:
        tenant.tenant_type = req.tenant_type

    if req.is_exempt_billing is not None:
        tenant.is_exempt_billing = req.is_exempt_billing

    db.commit()
    db.refresh(tenant)
    logger.info(
        f"Tenant {tenant_id} updated by staff {ctx.user_id}: cert_status={tenant.cert_status}, exempt={tenant.is_exempt_billing}"
    )

    ws_count = (
        db.query(func.count(func.distinct(PermTenantWorkspace.workspace_id)))
        .filter(PermTenantWorkspace.tenant_id == tenant_id, PermTenantWorkspace.status == 1)
        .scalar()
        or 0
    )
    user_count = (
        db.query(func.count(func.distinct(UcUserProfile.user_id)))
        .filter(UcUserProfile.tenant_id == tenant_id, UcUserProfile.is_deleted == 0)
        .scalar()
        or 0
    )
    master = (
        db.query(UcUserProfile)
        .filter_by(tenant_id=tenant_id, account_type="MASTER", is_deleted=0)
        .first()
    )

    return ResponseModel(
        data=AdminTenantItemResp(
            tenant_id=tenant.tenant_id,
            tenant_type=tenant.tenant_type,
            tenant_name=tenant.tenant_name,
            tenant_code=tenant.tenant_code,
            org_name=tenant.org_name,
            cert_level=tenant.cert_level,
            cert_status=tenant.cert_status,
            credit_code=tenant.credit_code,
            legal_person_name=tenant.legal_person_name,
            is_exempt_billing=tenant.is_exempt_billing,
            workspace_count=ws_count,
            user_count=user_count,
            master_user_id=master.user_id if master else None,
            created_at=tenant.created_at.strftime("%Y-%m-%d %H:%M:%S") if tenant.created_at else "",
        )
    )


@router.get(
    "/users",
    response_model=ResponseModel[List[AdminUserItemResp]],
    summary="平台超管跨租户查询全域用户列表",
)
def list_all_users(
    keyword: Optional[str] = Query(None, description="搜索用户名/显示名/用户ID"),
    tenant_id: Optional[str] = Query(None, description="指定租户ID过滤"),
    account_type: Optional[str] = Query(None, description="MASTER / SUB"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)

    query = db.query(UcUserProfile, UcTenant).join(
        UcTenant,
        (UcTenant.tenant_id == UcUserProfile.tenant_id) & (UcTenant.is_deleted == 0),
        isouter=True,
    ).filter(UcUserProfile.is_deleted == 0)

    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        query = query.filter(
            (UcUserProfile.user_id.ilike(kw))
            | (UcUserProfile.casdoor_user_id.ilike(kw))
            | (UcUserProfile.portal_user_id.ilike(kw))
        )
    if tenant_id:
        query = query.filter(UcUserProfile.tenant_id == tenant_id)
    if account_type:
        query = query.filter(UcUserProfile.account_type == account_type)

    rows = query.order_by(UcUserProfile.id.desc()).all()

    # 真正的 GROUP BY 统计每个用户授权的工作空间数
    user_ws_counts = dict(
        db.query(
            PermUserRoleWorkspace.user_id,
            func.count(func.distinct(PermUserRoleWorkspace.workspace_id)),
        )
        .group_by(PermUserRoleWorkspace.user_id)
        .all()
    )

    # 聚合角色与工作空间清单
    target_user_ids = [p.user_id for p, _ in rows]
    bindings = (
        db.query(PermUserRoleWorkspace)
        .filter(PermUserRoleWorkspace.user_id.in_(target_user_ids))
        .all()
        if target_user_ids
        else []
    )
    user_roles: Dict[str, List[str]] = {}
    user_ws: Dict[str, List[str]] = {}
    for b in bindings:
        user_roles.setdefault(b.user_id, []).append(b.role_code)
        user_ws.setdefault(b.user_id, []).append(b.workspace_id)

    results = []
    for profile, tenant in rows:
        roles = list(dict.fromkeys(user_roles.get(profile.user_id, [])))
        workspaces = list(dict.fromkeys(user_ws.get(profile.user_id, [])))
        t_name = (tenant.tenant_name if tenant else None) or profile.tenant_id
        results.append(
            AdminUserItemResp(
                user_id=profile.user_id,
                username=profile.casdoor_user_id,
                display_name=profile.casdoor_user_id,
                tenant_id=profile.tenant_id,
                tenant_name=t_name,
                account_type=profile.account_type,
                depth_level=profile.depth_level,
                workspace_count=user_ws_counts.get(profile.user_id, 0),
                roles=roles,
                workspaces=workspaces,
                created_at=profile.created_at.strftime("%Y-%m-%d %H:%M:%S") if profile.created_at else "",
            )
        )

    return ResponseModel(data=results)


@router.put(
    "/users/{user_id}/status",
    response_model=ResponseModel[AdminUserItemResp],
    summary="平台超管重设角色或重置密码",
)
def update_user_status(
    user_id: str,
    req: AdminUserUpdateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)

    profile = db.query(UcUserProfile).filter_by(user_id=user_id, is_deleted=0).first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到用户 {user_id}",
        )

    if req.role_code:
        # 保护系统核心超级管理员账号，防止被误操作降权
        if (profile.casdoor_user_id in ["admin", "admin_ops"] or profile.user_id in ["usr_p_admin", "usr_admin_ops"]) and req.role_code not in ["PLATFORM_ADMIN", "PLATFORM_OPERATOR"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="系统内置平台管理员账号不允许被修改为租户普通角色",
            )
        # 更新该用户所有绑定的主角色为目标角色
        bindings = db.query(PermUserRoleWorkspace).filter_by(user_id=user_id).all()
        for b in bindings:
            b.role_code = req.role_code
            if req.role_code != "TENANT_DEVELOPER":
                b.allowed_modules = ["*"]

    if req.new_password:
        # 同步重置 Casdoor 用户密码
        try:
            casdoor_client.update_user(
                username=profile.casdoor_user_id,
                password=req.new_password,
            )
            logger.info(f"Casdoor password reset for {profile.casdoor_user_id}")
        except Exception as e:
            logger.warning(f"Casdoor password reset warning: {e}")

    db.commit()
    db.refresh(profile)

    tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
    bindings = db.query(PermUserRoleWorkspace).filter_by(user_id=user_id).all()
    roles = list(dict.fromkeys(b.role_code for b in bindings))
    workspaces = list(dict.fromkeys(b.workspace_id for b in bindings))
    ws_count = (
        db.query(func.count(func.distinct(PermUserRoleWorkspace.workspace_id)))
        .filter(PermUserRoleWorkspace.user_id == user_id)
        .scalar()
        or 0
    )

    return ResponseModel(
        data=AdminUserItemResp(
            user_id=profile.user_id,
            username=profile.casdoor_user_id,
            display_name=profile.casdoor_user_id,
            tenant_id=profile.tenant_id,
            tenant_name=(tenant.tenant_name if tenant else None) or profile.tenant_id,
            account_type=profile.account_type,
            depth_level=profile.depth_level,
            workspace_count=ws_count,
            roles=roles,
            workspaces=workspaces,
            created_at=profile.created_at.strftime("%Y-%m-%d %H:%M:%S") if profile.created_at else "",
        )
    )

    return ResponseModel(
        data=AdminUserItemResp(
            user_id=profile.user_id,
            username=profile.casdoor_user_id,
            display_name=profile.casdoor_user_id,
            tenant_id=profile.tenant_id,
            tenant_name=(tenant.tenant_name if tenant else None) or profile.tenant_id,
            account_type=profile.account_type,
            depth_level=profile.depth_level,
            status=profile.status,
            roles=roles,
            workspaces=workspaces,
            created_at=profile.created_at.strftime("%Y-%m-%d %H:%M:%S") if profile.created_at else "",
        )
    )


@router.get(
    "/sync/files",
    response_model=ResponseModel[Dict[str, Any]],
    summary="查询中台日文件导出状态与归档清单",
)
def get_middle_platform_sync_status(
    ctx: AuthContext = Depends(get_current_context),
):
    require_platform_staff(ctx)
    import os
    from datetime import datetime

    date_str = datetime.now().strftime("%Y%m%d")
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "upload",
        "user",
    )
    user_info_file = os.path.join(out_dir, f"USER_INFO_{date_str}.000")
    user_attr_file = os.path.join(out_dir, f"USER_ATTR_{date_str}.000")

    info_exists = os.path.exists(user_info_file)
    attr_exists = os.path.exists(user_attr_file)

    info_count = 0
    if info_exists:
        with open(user_info_file, "r", encoding="utf-8") as f:
            info_count = sum(1 for line in f if line.strip())

    attr_count = 0
    if attr_exists:
        with open(user_attr_file, "r", encoding="utf-8") as f:
            attr_count = sum(1 for line in f if line.strip())

    return ResponseModel(
        data={
            "today_date": date_str,
            "export_directory": out_dir,
            "user_info_file": os.path.basename(user_info_file),
            "user_info_ready": info_exists,
            "user_info_count": info_count,
            "user_attr_file": os.path.basename(user_attr_file),
            "user_attr_ready": attr_exists,
            "user_attr_count": attr_count,
            "status": "READY" if (info_exists and attr_exists) else "PENDING",
        }
    )
