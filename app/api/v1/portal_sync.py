import logging
import json
import uuid
import httpx
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.casdoor_client import casdoor_client
from app.core.portal_client import portal_client
from app.core.security import create_internal_jwt, generate_gateway_headers
from app.api.v1.workspace_perm import filter_modules_for_platform
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.portal_sync import (
    PortalUserSyncReq,
    PortalUserSyncResp,
    EnterpriseApprovedReq,
    EnterpriseApprovedResp,
    PortalSubAccountSyncReq,
    PortalSubAccountSyncResp,
    SubLoginReq,
    SubLoginResp,
    QueryUserInfoReq,
    QueryUserInfoResp,
    PortalUserPermContextReq,
    AuthorizedWorkspacePermItem,
    PortalUserPermContextResp,
)

VALID_SUB_SYNC_ROLES = {
    "TENANT_ADMIN",
    "TENANT_DEVELOPER",
    "TENANT_FINANCE",
    "TENANT_VIEWER",
}

logger = logging.getLogger("iam-service.portal_sync")

router = APIRouter(tags=["门户同步与SSO"])


def sync_user_internal(db: Session, req: PortalUserSyncReq) -> PortalUserSyncResp:
    """
    Idempotent logic to synchronize portal user with Casdoor and local extensions.
    Reused by both POST /sync/user and JIT lazy-loading.
    """
    # 1. Check if user already exists
    profile = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.portal_user_id == req.portal_user_id,
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )

    if profile:
        # Existing user - fetch tenant and default workspace
        tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
        if req.is_platform_admin and tenant:
            # 自动修复平台管理员历史残留的个人租户与空间名称
            if tenant.tenant_type != "ENTERPRISE" or "个人空间" in (tenant.tenant_name or ""):
                tenant.tenant_type = "ENTERPRISE"
                tenant.tenant_name = req.company_name or "数智中台运营中心"
                tenant.cert_status = 2
                tenant.is_exempt_billing = 1
                db.commit()

        ws = (
            db.query(PermTenantWorkspace)
            .filter_by(tenant_id=profile.tenant_id, is_default=1)
            .first()
        )
        ws_id = ws.workspace_id if ws else ""
        return PortalUserSyncResp(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            tenant_type=tenant.tenant_type if tenant else "ENTERPRISE",
            default_workspace_id=ws_id,
            is_new_user=False,
        )

    # 2. New user registration / first synchronization
    # Step A: Sync to Casdoor
    casdoor_client.create_user(
        username=req.username,
        display_name=req.display_name,
        phone=req.phone or "",
        email=req.email or "",
    )

    # Step B: Determine IDs
    user_id = f"usr_{req.portal_user_id}"
    tenant_id = f"ten_{req.portal_user_id}"
    workspace_id = f"ws_default_{req.portal_user_id}"

    # Step C: Create Tenant (UcTenant)
    if req.is_enterprise:
        tenant_type = "ENTERPRISE"
        cert_level = 2
        cert_status = 2
        tenant_name = req.company_name or f"{req.display_name}的企业"
    else:
        tenant_type = "PERSONAL"
        cert_level = 0
        cert_status = 0
        tenant_name = f"{req.display_name}的个人空间"

    tenant = db.query(UcTenant).filter_by(tenant_id=tenant_id).first()
    if not tenant:
        tenant = UcTenant(
            tenant_id=tenant_id,
            tenant_type=tenant_type,
            org_name=f"org_{req.portal_user_id}",
            tenant_code=f"code_{req.portal_user_id}",
            tenant_name=tenant_name,
            cert_level=cert_level,
            cert_status=cert_status,
            legal_person_name=req.display_name if req.is_enterprise else None,
        )
        db.add(tenant)
        db.flush()

    # Step D: Create Default Workspace (PermTenantWorkspace)
    ws = db.query(PermTenantWorkspace).filter_by(workspace_id=workspace_id).first()
    if not ws:
        ws = PermTenantWorkspace(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            workspace_name="默认工作空间",
            is_default=1,
            k8s_namespace=f"ns-{req.portal_user_id}",
            quota_config_json={"cpu": "8C", "memory": "32G", "storage": "100G"},
            status=1,
        )
        db.add(ws)
        db.flush()

    # Step E: Create User Profile (UcUserProfile)
    profile = UcUserProfile(
        user_id=user_id,
        portal_user_id=req.portal_user_id,
        casdoor_user_id=req.username,
        tenant_id=tenant_id,
        account_type="MASTER",
        parent_user_id="",
        tree_path=f"/{user_id}/",
        depth_level=1,
        quota_share_enabled=1,
        status=1,
    )
    db.add(profile)
    db.flush()

    # Step F: Bind Initial Role and Workspace (PermUserRoleWorkspace)
    initial_role = "PLATFORM_ADMIN" if req.is_platform_admin else "TENANT_ADMIN"
    role_binding = PermUserRoleWorkspace(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace_id,
        role_code=initial_role,
        allowed_modules=["*"],
    )
    db.add(role_binding)

    db.commit()
    logger.info(f"Successfully synchronized new portal user: {user_id}, tenant: {tenant_id}")

    return PortalUserSyncResp(
        user_id=user_id,
        tenant_id=tenant_id,
        tenant_type=tenant_type,
        default_workspace_id=workspace_id,
        is_new_user=True,
    )


@router.post(
    "/sync/user",
    response_model=ResponseModel[PortalUserSyncResp],
    summary="门户注册/登录静默双写同步（幂等）",
)
def sync_user(req: PortalUserSyncReq, db: Session = Depends(get_db)):
    """
    接收门户系统用户注册或登录时的同步请求。
    若首次同步，自动在 Casdoor 建号、创建租户、工作空间及管理员角色；若已存在则直接返回关联上下文。
    """
    resp = sync_user_internal(db, req)
    return ResponseModel(data=resp)


@router.post(
    "/sync/enterprise-approved",
    response_model=ResponseModel[EnterpriseApprovedResp],
    summary="门户后台企业认证通过回调",
)
def enterprise_approved(req: EnterpriseApprovedReq, db: Session = Depends(get_db)):
    """
    门户企业认证审核通过后的回调接口。
    将对应租户升级为 ENTERPRISE 租户（cert_level=2, cert_status=2），解锁子账号创建功能。
    """
    profile = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.portal_user_id == req.portal_user_id,
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"门户用户 {req.portal_user_id} 尚未同步建档，请先调用 /sync/user",
        )

    tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"用户对应的租户 {profile.tenant_id} 不存在",
        )

    # 升级企业状态
    tenant.tenant_type = "ENTERPRISE"
    tenant.cert_level = 2
    tenant.cert_status = 2
    tenant.tenant_name = req.company_name
    tenant.credit_code = req.credit_code
    tenant.legal_person_name = req.legal_person_name

    db.commit()
    db.refresh(tenant)
    logger.info(f"Tenant {tenant.tenant_id} upgraded to ENTERPRISE: {tenant.tenant_name}")

    return ResponseModel(
        data=EnterpriseApprovedResp(
            tenant_id=tenant.tenant_id,
            tenant_type=tenant.tenant_type,
            tenant_name=tenant.tenant_name,
            cert_level=tenant.cert_level,
            cert_status=tenant.cert_status,
        )
    )


@router.post(
    "/sync/sub-account",
    response_model=ResponseModel[PortalSubAccountSyncResp],
    summary="门户子账号申请审核通过同步（幂等）",
)
def sync_sub_account(req: PortalSubAccountSyncReq, db: Session = Depends(get_db)):
    """
    门户系统在子账号申请审核通过后回调本接口，将子账号同步至权限中心与 Casdoor，
    写入物化路径树并绑定初始工作空间与角色。支持按 portal_user_id 幂等重试。
    """
    if req.role_code not in VALID_SUB_SYNC_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的租户角色编码: {req.role_code}",
        )
    if req.status not in (1, 2):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="账号状态仅支持 1(正常) 或 2(冻结)",
        )

    # 1. 定位所属企业主账号与租户
    master_profile = None
    if req.master_portal_user_id and req.master_portal_user_id.strip():
        master_profile = (
            db.query(UcUserProfile)
            .filter(
                UcUserProfile.portal_user_id == req.master_portal_user_id.strip(),
                UcUserProfile.is_deleted == 0,
            )
            .first()
        )
    elif req.tenant_id and req.tenant_id.strip():
        master_profile = (
            db.query(UcUserProfile)
            .filter(
                UcUserProfile.tenant_id == req.tenant_id.strip(),
                UcUserProfile.account_type == "MASTER",
                UcUserProfile.is_deleted == 0,
            )
            .first()
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="master_portal_user_id 与 tenant_id 至少需提供一个",
        )

    if not master_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到所属企业主账号，请先调用 /api/v1/sync/user 同步企业主账号",
        )

    tenant = db.query(UcTenant).filter_by(tenant_id=master_profile.tenant_id).first()
    if not tenant or tenant.tenant_type != "ENTERPRISE" or tenant.cert_level < 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="所属租户尚未完成企业实名认证，无法同步企业子账号",
        )

    # 2. 幂等校验：若门户子账号已同步过则直接返回
    existing_by_portal = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.portal_user_id == req.portal_user_id,
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if existing_by_portal:
        if existing_by_portal.tenant_id != tenant.tenant_id or existing_by_portal.account_type != "SUB":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"门户用户 {req.portal_user_id} 已归属其他租户或为主账号，无法重复同步为当前企业子账号",
            )
        if existing_by_portal.status != req.status:
            existing_by_portal.status = req.status
            db.commit()
        bindings = (
            db.query(PermUserRoleWorkspace)
            .filter_by(user_id=existing_by_portal.user_id, tenant_id=tenant.tenant_id)
            .all()
        )
        return ResponseModel(
            data=PortalSubAccountSyncResp(
                user_id=existing_by_portal.user_id,
                portal_user_id=existing_by_portal.portal_user_id or req.portal_user_id,
                username=existing_by_portal.casdoor_user_id,
                display_name=req.display_name,
                tenant_id=tenant.tenant_id,
                account_type=existing_by_portal.account_type,
                parent_user_id=existing_by_portal.parent_user_id,
                tree_path=existing_by_portal.tree_path,
                depth_level=existing_by_portal.depth_level,
                workspaces=list(dict.fromkeys(b.workspace_id for b in bindings)),
                role_code=bindings[0].role_code if bindings else req.role_code,
                allowed_modules=list(dict.fromkeys(m for b in bindings for m in (b.allowed_modules or []))),
                status=existing_by_portal.status,
                is_new_user=False,
            )
        )

    # 校验用户名唯一性
    existing_uname = (
        db.query(UcUserProfile)
        .filter(
            (UcUserProfile.casdoor_user_id == req.username)
            | (UcUserProfile.user_id == req.username),
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if existing_uname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"子账号用户名 '{req.username}' 已被占用",
        )

    # 3. 确定直接上级与物化路径
    parent_profile = master_profile
    if req.parent_portal_user_id and req.parent_portal_user_id.strip():
        p_key = req.parent_portal_user_id.strip()
        found_parent = (
            db.query(UcUserProfile)
            .filter(
                (UcUserProfile.portal_user_id == p_key) | (UcUserProfile.user_id == p_key),
                UcUserProfile.tenant_id == tenant.tenant_id,
                UcUserProfile.is_deleted == 0,
            )
            .first()
        )
        if not found_parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"指定的直接上级账号 {p_key} 不存在或不属于当前企业",
            )
        parent_profile = found_parent

    candidate_uid = f"usr_sub_{req.portal_user_id}"
    if len(candidate_uid) > 64 or db.query(UcUserProfile).filter_by(user_id=candidate_uid).first():
        candidate_uid = f"usr_sub_{uuid.uuid4().hex[:8]}"

    tree_path = f"{parent_profile.tree_path}{candidate_uid}/"
    depth_level = parent_profile.depth_level + 1

    # 4. 校验目标工作空间（未指定则自动绑定企业默认空间）
    if req.workspace_ids:
        target_ws_ids = list(dict.fromkeys(req.workspace_ids))
        valid_ws = (
            db.query(PermTenantWorkspace.workspace_id)
            .filter(
                PermTenantWorkspace.tenant_id == tenant.tenant_id,
                PermTenantWorkspace.workspace_id.in_(target_ws_ids),
                PermTenantWorkspace.status == 1,
            )
            .all()
        )
        valid_set = {r[0] for r in valid_ws}
        invalid_ws = [w for w in target_ws_ids if w not in valid_set]
        if invalid_ws:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"工作空间不存在或不属于当前企业: {', '.join(invalid_ws)}",
            )
    else:
        default_ws = (
            db.query(PermTenantWorkspace)
            .filter_by(tenant_id=tenant.tenant_id, is_default=1, status=1)
            .first()
        )
        target_ws_ids = [default_ws.workspace_id] if default_ws else []

    # 5. 同步至 Casdoor 用户池
    casdoor_client.create_user(
        username=req.username,
        display_name=req.display_name,
        phone=req.phone or "",
        email=req.email or "",
        password=req.password or f"P@{uuid.uuid4().hex[:12]}!",
    )

    # 6. 写入本地 uc_user_profile 与工作空间权限绑定
    sub_profile = UcUserProfile(
        user_id=candidate_uid,
        portal_user_id=req.portal_user_id,
        casdoor_user_id=req.username,
        tenant_id=tenant.tenant_id,
        account_type="SUB",
        parent_user_id=parent_profile.user_id,
        tree_path=tree_path,
        depth_level=depth_level,
        quota_share_enabled=1,
        status=req.status,
    )
    db.add(sub_profile)
    db.flush()

    if req.role_code == "TENANT_DEVELOPER":
        allowed_modules = req.allowed_modules if req.allowed_modules is not None else ["*"]
    else:
        allowed_modules = ["*"]

    for ws_id in target_ws_ids:
        db.add(
            PermUserRoleWorkspace(
                tenant_id=tenant.tenant_id,
                user_id=candidate_uid,
                workspace_id=ws_id,
                role_code=req.role_code,
                allowed_modules=allowed_modules,
            )
        )

    db.commit()
    logger.info(
        f"Portal sub-account synced: user_id={candidate_uid}, portal_uid={req.portal_user_id}, tenant={tenant.tenant_id}"
    )

    return ResponseModel(
        data=PortalSubAccountSyncResp(
            user_id=candidate_uid,
            portal_user_id=req.portal_user_id,
            username=req.username,
            display_name=req.display_name,
            tenant_id=tenant.tenant_id,
            account_type="SUB",
            parent_user_id=parent_profile.user_id,
            tree_path=tree_path,
            depth_level=depth_level,
            workspaces=target_ws_ids,
            role_code=req.role_code,
            allowed_modules=allowed_modules,
            status=req.status,
            is_new_user=True,
        )
    )


def _map_portal_sso_user(raw: Dict[str, Any]) -> Dict[str, Any]:
    """将门户 /api/sso/validateTicket 或目录同步返回字段映射为 PortalUserSyncReq 参数。"""
    portal_uid = str(raw.get("userId") or raw.get("portal_user_id") or raw.get("userName") or "")
    uname = str(raw.get("userName") or raw.get("username") or portal_uid)
    dname = str(raw.get("name") or raw.get("displayName") or raw.get("display_name") or uname)
    phone = raw.get("phone") or raw.get("mobile") or ""
    email = raw.get("email") or ""
    company_id = str(raw.get("companyId") or raw.get("companyCode") or "").strip()
    company_name = str(raw.get("companyName") or raw.get("company_name") or "").strip()
    role_name = str(raw.get("roleName") or "").strip()

    is_admin = bool(
        raw.get("is_platform_admin")
        or any(k in role_name for k in ("平台", "超级管理员", "超管"))
        or (role_name == "管理员" and company_name in ("门户", "数智中台运营中心", ""))
        or uname.lower() == "admin"
    )
    is_ent = bool(is_admin or company_id or company_name)
    return {
        "portal_user_id": portal_uid,
        "username": uname,
        "display_name": dname,
        "phone": str(phone) if phone is not None else "",
        "email": str(email) if email is not None else "",
        "is_platform_admin": is_admin,
        "is_enterprise": is_ent,
        "company_name": "数智中台运营中心" if (is_admin and company_name in ("", "门户")) else company_name,
    }


@router.get(
    "/sso/exchange-iframe-session",
    response_model=ResponseModel[Dict[str, Any]],
    summary="iframe 嵌入页面免密换票（支持门户 sessionId 两步换票）",
)
def exchange_iframe_session(
    sessionId: Optional[str] = Query(None, description="门户跳转携带的 sessionId（优先）"),
    portal_token: Optional[str] = Query(None, description="门户登录 Token 或开发调试凭证"),
    clientId: Optional[str] = Query(None, description="可选覆盖门户分配的子平台 clientId"),
    db: Session = Depends(get_db),
):
    """
    供 8001/ui/* 前端页面加载时调用：
    1. 若已配置 PORTAL_SSO_BASE_URL（且非本地 token_p_ 调试凭证），按《门户-身份体系接口文档》执行标准两步换票：
       GET /api/sso/login (sessionId -> 20s ticket) -> GET /api/sso/validateTicket (ticket -> 用户与企业信息)；
    2. 若门户返回 ticket 为 null（未登录），返回 HTTP 401 并携带门户统一登录页地址 login_url；
    3. 支持兼容单步 PORTAL_USERINFO_URL 或本地 Mock 凭证；
    4. 内部自动执行 JIT 懒加载同步并签发 8001 内部访问 JWT。
    """
    credential = (sessionId or portal_token or "").strip()
    if not credential and not portal_client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少门户会话参数 sessionId 或 portal_token",
        )

    user_info: Optional[Dict[str, Any]] = None
    portal_auth_token = ""
    redirect_url = ""
    is_dev_mock_token = credential.startswith("token_p_")

    # 1. 标准两步门户认证协议 (/api/sso/login -> /api/sso/validateTicket)
    if portal_client.is_configured() and not is_dev_mock_token:
        try:
            sso_res = portal_client.exchange_session_for_user(credential, client_id=clientId)
        except Exception as e:
            logger.error(f"Portal SSO 2-step exchange error: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"调用门户统一认证服务异常: {e}",
            )

        if not sso_res.get("authenticated") or not sso_res.get("user"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "message": "门户未登录或 sessionId 已失效，请先在门户完成登录",
                    "login_url": sso_res.get("login_url") or settings.PORTAL_LOGIN_URL,
                },
            )

        raw_user = sso_res["user"]
        portal_auth_token = str(raw_user.get("token") or "")
        redirect_url = str(raw_user.get("redirectUrl") or "")
        user_info = _map_portal_sso_user(raw_user)

    # 2. 兼容单步 PORTAL_USERINFO_URL 配置
    if not user_info and settings.PORTAL_USERINFO_URL and not is_dev_mock_token:
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(
                    settings.PORTAL_USERINFO_URL,
                    headers={"Authorization": f"Bearer {credential}"},
                )
                if res.status_code == 200:
                    user_info = res.json()
        except Exception as e:
            logger.warning(f"Failed to query portal userinfo with token: {e}")

    # 3. Mock / Dev Fallback: 本地调试模式解析 token_p_xxx / p_xxx
    is_admin = False
    if not user_info:
        clean_token = credential.replace("Bearer ", "").strip()
        parts = clean_token.split("_")
        if len(parts) >= 3 and parts[0] == "token":
            portal_uid = f"{parts[1]}_{parts[2]}"
            uname = parts[2] if len(parts) > 2 else "portal_user"
        elif clean_token.startswith("p_"):
            portal_uid = clean_token
            uname = f"user_{clean_token}"
        else:
            portal_uid = clean_token[:32]
            uname = f"user_{clean_token[:8]}"

        is_admin = ("admin" in clean_token.lower() or "operator" in clean_token.lower())
        user_info = {
            "portal_user_id": portal_uid,
            "username": uname,
            "display_name": f"平台管理员_{uname}" if is_admin else f"用户_{uname}",
            "phone": "13800000000",
            "is_platform_admin": is_admin,
            "is_enterprise": True,
            "company_name": "数智中台运营中心" if is_admin else f"企业_{uname}",
        }
    else:
        is_admin = bool(user_info.get("is_platform_admin") or "admin" in str(user_info.get("username", "")).lower())

    # 4. JIT Lazy loading synchronization
    sync_req = PortalUserSyncReq(**user_info)
    sync_resp = sync_user_internal(db, sync_req)

    # 5. Fetch user profile, tenant & role
    profile = db.query(UcUserProfile).filter_by(user_id=sync_resp.user_id).first()
    tenant = db.query(UcTenant).filter_by(tenant_id=sync_resp.tenant_id).first()
    role_binding = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            user_id=sync_resp.user_id,
            workspace_id=sync_resp.default_workspace_id,
        )
        .first()
    )
    if not role_binding:
        role_binding = (
            db.query(PermUserRoleWorkspace)
            .filter_by(user_id=sync_resp.user_id, tenant_id=sync_resp.tenant_id)
            .first()
        )
    account_type = profile.account_type if profile else "MASTER"
    default_role = "TENANT_ADMIN" if account_type == "MASTER" else "TENANT_VIEWER"
    role_code = role_binding.role_code if role_binding else default_role

    # 平台超管/运营自动纠偏与自愈保障：若为平台管理员，强制平台角色并纠偏历史被改乱的数据
    is_staff_user = bool(is_admin or (sync_req and sync_req.is_platform_admin))
    if is_staff_user:
        role_code = "PLATFORM_ADMIN"
        if role_binding and role_binding.role_code != "PLATFORM_ADMIN":
            role_binding.role_code = "PLATFORM_ADMIN"
            role_binding.allowed_modules = ["*"]
            db.commit()
        if tenant and (tenant.tenant_type != "ENTERPRISE" or "个人空间" in (tenant.tenant_name or "")):
            tenant.tenant_type = "ENTERPRISE"
            tenant.tenant_name = "数智中台运营中心"
            tenant.cert_status = 2
            tenant.is_exempt_billing = 1
            db.commit()
            tenant_name = tenant.tenant_name
        else:
            tenant_name = tenant.tenant_name if tenant else "数智中台运营中心"
    else:
        tenant_name = tenant.tenant_name if tenant else sync_resp.tenant_id
    username = profile.casdoor_user_id if profile else sync_resp.user_id

    # 6. Issue internal JWT
    jwt_claims = {
        "user_id": sync_resp.user_id,
        "tenant_id": sync_resp.tenant_id,
        "portal_user_id": sync_req.portal_user_id,
        "account_type": account_type,
        "role_code": role_code,
        "workspace_id": sync_resp.default_workspace_id,
    }
    internal_token = create_internal_jwt(jwt_claims, expires_in=86400)

    resp_data: Dict[str, Any] = {
        "token": internal_token,
        "user_id": sync_resp.user_id,
        "username": username,
        "account_type": account_type,
        "tenant_id": sync_resp.tenant_id,
        "tenant_name": tenant_name,
        "tenant_type": sync_resp.tenant_type,
        "default_workspace_id": sync_resp.default_workspace_id,
        "role_code": role_code,
        "expires_in": 86400,
    }
    if portal_auth_token:
        resp_data["portal_token"] = portal_auth_token
    if redirect_url:
        resp_data["redirect_url"] = redirect_url

    return ResponseModel(data=resp_data)


@router.post(
    "/sync/pull-from-portal",
    response_model=ResponseModel[Dict[str, Any]],
    summary="从门户全量拉取用户与企业目录并同步入库",
)
def pull_directory_from_portal(
    client_id: Optional[str] = Query(None, description="可选覆盖 clientId"),
    db: Session = Depends(get_db),
):
    """
    主动调用门户《身份体系接口文档》的 4、5 号目录接口：
    - GET /api/company/listAuthCompanyList
    - GET /api/user/listUserInfoList
    校验 authentication = MD5(clientId + 4c4a1c7d52) 后幂等同步入库。
    """
    if not portal_client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未配置 PORTAL_SSO_BASE_URL，无法连接门户拉取目录",
        )

    companies = portal_client.list_auth_companies(client_id=client_id)
    users = portal_client.list_user_info(client_id=client_id)

    synced_companies = 0
    for c in companies:
        code = str(c.get("companyCode") or "").strip()
        name = str(c.get("companyName") or "").strip()
        if not code:
            continue
        tenant = db.query(UcTenant).filter(
            (UcTenant.tenant_code == code) | (UcTenant.tenant_id == f"ten_ent_{code}")
        ).first()
        if not tenant:
            tenant = UcTenant(
                tenant_id=f"ten_ent_{code}",
                tenant_type="ENTERPRISE",
                org_name=f"org_{code}",
                tenant_code=code,
                tenant_name=name or code,
                cert_level=2,
                cert_status=2,
            )
            db.add(tenant)
            synced_companies += 1
    db.commit()

    synced_users = 0
    for u in users:
        mapped = _map_portal_sso_user(u)
        if not mapped["portal_user_id"]:
            continue
        sync_resp = sync_user_internal(db, PortalUserSyncReq(**mapped))
        if sync_resp.is_new_user:
            synced_users += 1

    return ResponseModel(
        data={
            "fetched_companies": len(companies),
            "new_companies_synced": synced_companies,
            "fetched_users": len(users),
            "new_users_synced": synced_users,
        }
    )


@router.post(
    "/sso/sub-login",
    response_model=ResponseModel[SubLoginResp],
    summary="[已废弃] 企业子账号登录门户兜底接口（子账号已统一由门户注册与认证，无需透传验密）",
    deprecated=True,
)
def sub_login(req: SubLoginReq, db: Session = Depends(get_db)):
    """
    [已废弃通道]
    注意：当前业务架构已明确子账号全生命周期收敛于门户端（申请、审核、密码设置及登录鉴权）。
    门户本地原生拥有子账号数据，不存在“本地查无账号透传给用户中心验密登录”的逻辑。
    本接口仅作向前兼容保留，新对接方严禁调用。
    """
    # 1. Check user profile in uc_user_profile
    profile = (
        db.query(UcUserProfile)
        .filter(
            (UcUserProfile.casdoor_user_id == req.username)
            | (UcUserProfile.user_id == req.username),
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="子账号不存在或已被注销",
        )

    # 2. Verify password with Casdoor
    is_valid = casdoor_client.verify_password(req.username, req.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="密码错误，认证失败",
        )

    # 3. Fetch tenant info
    tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
    tenant_name = tenant.tenant_name if tenant else ""

    # 4. Fetch roles and workspaces
    role_bindings = (
        db.query(PermUserRoleWorkspace)
        .filter_by(user_id=profile.user_id)
        .all()
    )
    roles = list({rb.role_code for rb in role_bindings}) if role_bindings else ["TENANT_VIEWER"]
    default_ws_id = role_bindings[0].workspace_id if role_bindings else ""

    return ResponseModel(
        data=SubLoginResp(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            tenant_name=tenant_name,
            account_type=profile.account_type,
            roles=roles,
            default_workspace_id=default_ws_id,
        )
    )


@router.post(
    "/customer/cuseser/v1",
    response_model=ResponseModel[QueryUserInfoResp],
    summary="用户信息查询接口 (国家中试基地标准规范 2.4.3)",
    operation_id="query_user_info_v1_cuseser_post",
)
@router.post(
    "/user/queryUserInfo",
    response_model=ResponseModel[QueryUserInfoResp],
    summary="用户信息查询接口 (国家中试基地标准规范 2.4.3)",
    operation_id="query_user_info_post",
)
@router.get(
    "/user/queryUserInfo",
    response_model=ResponseModel[QueryUserInfoResp],
    summary="用户信息查询接口 (GET 支持)",
    operation_id="query_user_info_get",
)
def query_user_info(
    req: Optional[QueryUserInfoReq] = None,
    user_id: Optional[str] = Query(None),
    portal_user_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    符合《国家工业中试基地与数智中台 统一接口规范》2.4.3 具象业务调用样例:
    入参支持 USER_ID / user_id / portal_user_id，返回标准 _QUERY_USER_INFO_RSP 结构。
    """
    target_uid = ""
    if req:
        target_uid = req.USER_ID or req.user_id or req.portal_user_id or ""
    if not target_uid:
        target_uid = user_id or portal_user_id or ""

    if not target_uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少用户标识参数 (USER_ID 或 user_id)",
        )

    profile = (
        db.query(UcUserProfile)
        .filter(
            (UcUserProfile.user_id == target_uid)
            | (UcUserProfile.portal_user_id == target_uid)
            | (UcUserProfile.casdoor_user_id == target_uid),
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到用户标识为 '{target_uid}' 的用户信息",
        )

    tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
    user_name = (tenant.tenant_name if tenant else None) or profile.casdoor_user_id

    resp = QueryUserInfoResp(
        BIZ_CODE="0000",
        BIZ_MSG="查询成功",
        USER_ID=profile.user_id,
        USER_NAME=user_name,
        PROVINCE_CODE="32",
        EPARCHY_CODE="3205",
        STATUS="ACTIVE" if profile.status == 1 else "INACTIVE",
    )
    return ResponseModel(data=resp, api_name="queryUserInfo")


@router.post(
    "/sync/export-data-middle-platform",
    response_model=ResponseModel[Dict[str, Any]],
    summary="数据中台对接日文件导出 (国家中试基地规范第3章)",
)
def export_data_middle_platform(
    target_dir: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    国家工业中试基地与数智中台 统一接口规范 3.2 数据上传明细接口:
    - 3.2.1 用户信息-日文件 (USER_INFO_{YYYYMMDD}.000): USER_ID|USER_NAME|PROVINCE_CODE|EPARCHY_CODE
    - 3.2.2 用户属性信息-日文件 (USER_ATTR_{YYYYMMDD}.000): USER_ID|ATTR_TYPE|ATTR_NAME|ATTR_VALUE
    - 3.6.3 原子性保障: 写入 .tmp 临时文件后，通过原子重命名完成。
    """
    import os
    from datetime import datetime

    date_str = datetime.now().strftime("%Y%m%d")
    out_dir = target_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "upload", "user")
    os.makedirs(out_dir, exist_ok=True)

    user_info_file = os.path.join(out_dir, f"USER_INFO_{date_str}.000")
    user_info_tmp = f"{user_info_file}.tmp"
    user_attr_file = os.path.join(out_dir, f"USER_ATTR_{date_str}.000")
    user_attr_tmp = f"{user_attr_file}.tmp"

    profiles = db.query(UcUserProfile).filter_by(is_deleted=0).all()
    user_lines = []
    attr_lines = []

    for p in profiles:
        tenant = db.query(UcTenant).filter_by(tenant_id=p.tenant_id).first()
        org_name = (tenant.tenant_name if tenant else None) or p.casdoor_user_id
        # 3.2.1 USER_ID|USER_NAME|PROVINCE_CODE|EPARCHY_CODE
        user_lines.append(f"{p.user_id}|{org_name}|32|3205\n")

        # 3.2.2 USER_ID|ATTR_TYPE|ATTR_NAME|ATTR_VALUE
        attr_lines.append(f"{p.user_id}|1|ACCOUNT_TYPE|{p.account_type}\n")
        if tenant:
            attr_lines.append(f"{p.user_id}|2|TENANT_TYPE|{tenant.tenant_type}\n")
            attr_lines.append(f"{p.user_id}|4|QUALIFICATION_LEVEL|LEVEL_{tenant.cert_level}\n")
            if tenant.credit_code:
                attr_lines.append(f"{p.user_id}|2|CREDIT_CODE|{tenant.credit_code}\n")

    # 3.6.3 原子写入 USER_INFO
    with open(user_info_tmp, "w", encoding="utf-8") as f:
        f.writelines(user_lines)
    if os.path.exists(user_info_file):
        os.remove(user_info_file)
    os.rename(user_info_tmp, user_info_file)

    # 3.6.3 原子写入 USER_ATTR
    with open(user_attr_tmp, "w", encoding="utf-8") as f:
        f.writelines(attr_lines)
    if os.path.exists(user_attr_file):
        os.remove(user_attr_file)
    os.rename(user_attr_tmp, user_attr_file)

    result_data = {
        "status": "SUCCESS",
        "date_str": date_str,
        "user_info_file": os.path.basename(user_info_file),
        "user_info_count": len(user_lines),
        "user_attr_file": os.path.basename(user_attr_file),
        "user_attr_count": len(attr_lines),
        "export_directory": out_dir,
    }
    return ResponseModel(data=result_data, api_name="exportDataMiddlePlatform")


def _resolve_master_user_id(profile: UcUserProfile) -> str:
    """从物化路径或上级标识解析所属企业主账号 user_id。"""
    if profile.account_type != "SUB":
        return profile.user_id
    segments = [s for s in (profile.tree_path or "").split("/") if s]
    if segments:
        return segments[0]
    return profile.parent_user_id or profile.user_id


def _build_portal_perm_context(
    db: Session,
    target_portal_uid: str,
    target_uid: str,
    workspace_id: Optional[str],
    target_platform: Optional[str],
) -> PortalUserPermContextResp:
    """聚合查询用户租户、主子溯源、空间权限与网关签名头（供门户核销 Ticket 时调用）。"""
    if not target_portal_uid and not target_uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少用户标识参数 (portal_user_id 或 user_id)",
        )

    query = db.query(UcUserProfile).filter(UcUserProfile.is_deleted == 0)
    if target_portal_uid and target_uid:
        profile = query.filter(
            (UcUserProfile.portal_user_id == target_portal_uid)
            | (UcUserProfile.user_id == target_uid)
        ).first()
    elif target_portal_uid:
        profile = query.filter(UcUserProfile.portal_user_id == target_portal_uid).first()
    else:
        profile = query.filter(UcUserProfile.user_id == target_uid).first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到对应的用户档案 (portal_user_id={target_portal_uid}, user_id={target_uid})",
        )

    tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id, is_deleted=0).first()
    tenant_name = tenant.tenant_name if tenant else profile.tenant_id
    tenant_type = tenant.tenant_type if tenant else "PERSONAL"
    cert_level = tenant.cert_level if tenant else 0
    master_user_id = _resolve_master_user_id(profile)

    # 查询租户下所有有效空间与该用户的全部权限绑定
    workspaces = (
        db.query(PermTenantWorkspace)
        .filter_by(tenant_id=profile.tenant_id, status=1)
        .order_by(PermTenantWorkspace.is_default.desc(), PermTenantWorkspace.id.asc())
        .all()
    )
    ws_map = {w.workspace_id: w for w in workspaces}

    bindings = (
        db.query(PermUserRoleWorkspace)
        .filter_by(tenant_id=profile.tenant_id, user_id=profile.user_id)
        .all()
    )
    binding_map: Dict[str, List[PermUserRoleWorkspace]] = {}
    is_platform_staff = False
    for b in bindings:
        binding_map.setdefault(b.workspace_id, []).append(b)
        if b.role_code in ("PLATFORM_ADMIN", "PLATFORM_OPERATOR"):
            is_platform_staff = True

    authorized_list: List[AuthorizedWorkspacePermItem] = []
    for w in workspaces:
        w_bindings = binding_map.get(w.workspace_id, [])
        if not w_bindings and not is_platform_staff:
            continue
        if w_bindings:
            role = w_bindings[0].role_code
            mods_raw = list({m for b in w_bindings for m in (b.allowed_modules or [])})
            mods = filter_modules_for_platform(mods_raw, target_platform)
        else:
            role = "PLATFORM_ADMIN"
            mods = ["*"]

        authorized_list.append(
            AuthorizedWorkspacePermItem(
                workspace_id=w.workspace_id,
                workspace_name=w.workspace_name,
                is_default=bool(w.is_default),
                k8s_namespace=w.k8s_namespace or "",
                role_code=role,
                allowed_modules=mods,
            )
        )

    # 选定当前激活空间
    active_item: Optional[AuthorizedWorkspacePermItem] = None
    if workspace_id:
        active_item = next((item for item in authorized_list if item.workspace_id == workspace_id), None)
        if not active_item:
            if is_platform_staff and workspace_id in ws_map:
                w_obj = ws_map[workspace_id]
                active_item = AuthorizedWorkspacePermItem(
                    workspace_id=w_obj.workspace_id,
                    workspace_name=w_obj.workspace_name,
                    is_default=bool(w_obj.is_default),
                    k8s_namespace=w_obj.k8s_namespace or "",
                    role_code="PLATFORM_ADMIN",
                    allowed_modules=["*"],
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"用户无权访问工作空间 {workspace_id}",
                )
    else:
        active_item = next((item for item in authorized_list if item.is_default), None)
        if not active_item and authorized_list:
            active_item = authorized_list[0]

    if active_item:
        active_ws_id = active_item.workspace_id
        active_ws_name = active_item.workspace_name
        k8s_ns = active_item.k8s_namespace
        primary_role = active_item.role_code
        active_mods = active_item.allowed_modules
        w_bindings = binding_map.get(active_ws_id, [])
        roles = list(dict.fromkeys(b.role_code for b in w_bindings)) if w_bindings else [primary_role]
    else:
        # 兜底（无任何空间绑定时）
        active_ws_id = ""
        active_ws_name = ""
        k8s_ns = ""
        primary_role = "PLATFORM_ADMIN" if is_platform_staff else ("TENANT_ADMIN" if profile.account_type == "MASTER" else "TENANT_VIEWER")
        roles = [primary_role]
        active_mods = ["*"] if is_platform_staff or profile.account_type == "MASTER" else []

    has_access = True
    if target_platform and primary_role == "TENANT_DEVELOPER" and len(active_mods) == 0:
        has_access = False

    gw_headers = generate_gateway_headers(
        tenant_id=profile.tenant_id,
        user_id=profile.user_id,
        master_user_id=master_user_id,
        workspace_id=active_ws_id,
        role_code=primary_role,
        shadow_account="",
    )

    return PortalUserPermContextResp(
        user_id=profile.user_id,
        portal_user_id=profile.portal_user_id,
        username=profile.casdoor_user_id,
        master_user_id=master_user_id,
        account_type=profile.account_type,
        tenant_id=profile.tenant_id,
        tenant_name=tenant_name,
        tenant_type=tenant_type,
        cert_level=cert_level,
        active_workspace_id=active_ws_id,
        active_workspace_name=active_ws_name,
        k8s_namespace=k8s_ns,
        role_code=primary_role,
        roles=roles,
        allowed_modules=active_mods,
        has_platform_access=has_access,
        authorized_workspaces=authorized_list,
        gateway_headers=gw_headers,
    )


@router.get(
    "/sync/user-perm-context",
    response_model=ResponseModel[PortalUserPermContextResp],
    summary="门户核销聚合查询用户全量权限上下文 (GET)",
)
def get_user_perm_context(
    portal_user_id: Optional[str] = Query(None, description="门户系统用户ID（与 user_id 二选一）"),
    user_id: Optional[str] = Query(None, description="用户中心全局用户ID（与 portal_user_id 二选一）"),
    workspace_id: Optional[str] = Query(None, description="目标工作空间ID（选填，不传取默认空间）"),
    target_platform: Optional[str] = Query(None, description="目标平台编码（选填，传则按平台裁剪模块白名单）"),
    db: Session = Depends(get_db),
):
    """
    供对外运营服务平台（门户）在沿用自身 Ticket 核销时调用：
    传入 portal_user_id，一次性获取租户、主子账号、角色、模块白名单及全部授权工作空间列表，
    由门户合并进原有核销 JSON 返回给子平台，子平台无需重改核销接口地址。
    """
    data = _build_portal_perm_context(db, portal_user_id or "", user_id or "", workspace_id, target_platform)
    return ResponseModel(data=data, api_name="getUserPermContext")


@router.post(
    "/sync/user-perm-context",
    response_model=ResponseModel[PortalUserPermContextResp],
    summary="门户核销聚合查询用户全量权限上下文 (POST / 兼容中台报文)",
)
def post_user_perm_context(
    req: PortalUserPermContextReq,
    db: Session = Depends(get_db),
):
    """POST 模式支持门户以 JSON Body 或中台标准 SYS_HEAD/SYS_BODY 报文查询用户权限上下文。"""
    data = _build_portal_perm_context(
        db,
        req.portal_user_id or "",
        req.user_id or "",
        req.workspace_id,
        req.target_platform,
    )
    return ResponseModel(data=data, api_name="getUserPermContext")


