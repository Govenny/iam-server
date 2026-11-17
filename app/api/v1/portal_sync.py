import logging
import json
import httpx
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.casdoor_client import casdoor_client
from app.core.security import create_internal_jwt
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
    SubLoginReq,
    SubLoginResp,
    QueryUserInfoReq,
    QueryUserInfoResp,
)

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
        ws = (
            db.query(PermTenantWorkspace)
            .filter_by(tenant_id=profile.tenant_id, is_default=1)
            .first()
        )
        ws_id = ws.workspace_id if ws else ""
        return PortalUserSyncResp(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            tenant_type=tenant.tenant_type if tenant else "PERSONAL",
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


@router.get(
    "/sso/exchange-iframe-session",
    response_model=ResponseModel[Dict[str, Any]],
    summary="iframe 嵌入页面免密换票",
)
def exchange_iframe_session(
    portal_token: str = Query(..., description="门户当前登录用户的 Token"),
    db: Session = Depends(get_db),
):
    """
    供 8001/ui/* 前端页面加载时调用。
    拿着 portal_token 反查门户信息或解析后，自动执行 JIT 懒加载兜底建档，签发 8001 内部访问 JWT。
    """
    user_info = None

    # 1. If PORTAL_USERINFO_URL is configured, query portal
    if settings.PORTAL_USERINFO_URL:
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(
                    settings.PORTAL_USERINFO_URL,
                    headers={"Authorization": f"Bearer {portal_token}"},
                )
                if res.status_code == 200:
                    user_info = res.json()
        except Exception as e:
            logger.warning(f"Failed to query portal userinfo with token: {e}")

    # 2. Mock / Dev Fallback: parse token string or extract identity
    if not user_info:
        # Support token format like "token_p_1001_zhangsan" or raw id
        clean_token = portal_token.replace("Bearer ", "").strip()
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

        user_info = {
            "portal_user_id": portal_uid,
            "username": uname,
            "display_name": f"用户_{uname}",
            "phone": "13800000000",
            "is_platform_admin": False,
            "is_enterprise": False,
            "company_name": "",
        }

    # 3. JIT Lazy loading synchronization
    sync_req = PortalUserSyncReq(**user_info)
    sync_resp = sync_user_internal(db, sync_req)

    # 4. Fetch user role
    role_binding = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            user_id=sync_resp.user_id,
            workspace_id=sync_resp.default_workspace_id,
        )
        .first()
    )
    role_code = role_binding.role_code if role_binding else "TENANT_ADMIN"

    # 5. Issue internal JWT
    jwt_claims = {
        "user_id": sync_resp.user_id,
        "tenant_id": sync_resp.tenant_id,
        "portal_user_id": sync_req.portal_user_id,
        "account_type": "MASTER",
        "role_code": role_code,
        "workspace_id": sync_resp.default_workspace_id,
    }
    internal_token = create_internal_jwt(jwt_claims, expires_in=86400)

    return ResponseModel(
        data={
            "token": internal_token,
            "user_id": sync_resp.user_id,
            "tenant_id": sync_resp.tenant_id,
            "tenant_type": sync_resp.tenant_type,
            "default_workspace_id": sync_resp.default_workspace_id,
            "role_code": role_code,
            "expires_in": 86400,
        }
    )


@router.post(
    "/sso/sub-login",
    response_model=ResponseModel[SubLoginResp],
    summary="企业子账号登录门户兜底接口",
)
def sub_login(req: SubLoginReq, db: Session = Depends(get_db)):
    """
    供门户登录接口在本地查不到账号时调用。
    向 Casdoor 校验密码后，返回子账号的完整租户、角色及空间信息。
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

