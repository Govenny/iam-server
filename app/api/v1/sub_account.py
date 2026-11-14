import uuid
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.casdoor_client import casdoor_client
from app.api.deps import AuthContext, get_current_context
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.sub_account import (
    SubAccountCreateReq,
    SubAccountCreateResp,
    SubAccountItemResp,
)

logger = logging.getLogger("iam-service.sub_account")

router = APIRouter(prefix="/uc", tags=["企业主子账号树"])


@router.post(
    "/sub-account/create",
    response_model=ResponseModel[SubAccountCreateResp],
    summary="创建企业子账号（写入物化路径树）",
)
def create_sub_account(
    req: SubAccountCreateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    企业主账号在当前租户下创建子账号。
    前置校验：操作人必须具备 TENANT_ADMIN / PLATFORM_ADMIN 权限，且所属租户必须为 ENTERPRISE。
    """
    # 1. 权限与租户前置校验
    tenant = ctx.tenant or db.query(UcTenant).filter_by(tenant_id=ctx.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="当前操作人所属租户不存在",
        )

    if tenant.tenant_type != "ENTERPRISE" or tenant.cert_level < 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前租户为个人租户或未完成企业认证，无法创建企业子账号。请先完成企业实名认证",
        )

    if ctx.role_code not in ["TENANT_ADMIN", "PLATFORM_ADMIN"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有企业管理员（TENANT_ADMIN）有权创建子账号",
        )

    # 2. 检查用户名唯一性
    existing = (
        db.query(UcUserProfile)
        .filter(
            (UcUserProfile.casdoor_user_id == req.username)
            | (UcUserProfile.user_id == req.username),
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"账号用户名 '{req.username}' 已被占用，请使用其他用户名",
        )

    # 3. 确定上级节点与物化路径
    parent_user_id = req.parent_user_id.strip() if req.parent_user_id else ""
    parent_profile = None

    if parent_user_id:
        parent_profile = (
            db.query(UcUserProfile)
            .filter_by(user_id=parent_user_id, tenant_id=ctx.tenant_id, is_deleted=0)
            .first()
        )
        if not parent_profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"指定的直接上级账号 {parent_user_id} 不存在或不属于当前企业",
            )
    else:
        # 默认挂在当前操作人的主账号下
        parent_profile = ctx.profile

    if not parent_profile:
        parent_profile = (
            db.query(UcUserProfile)
            .filter_by(tenant_id=ctx.tenant_id, account_type="MASTER", is_deleted=0)
            .first()
        )

    new_user_id = f"usr_sub_{uuid.uuid4().hex[:8]}"
    parent_path = parent_profile.tree_path if parent_profile else f"/{ctx.user_id}/"
    tree_path = f"{parent_path}{new_user_id}/"
    depth_level = (parent_profile.depth_level + 1) if parent_profile else 2

    # 4. 同步至 Casdoor 用户池
    casdoor_res = casdoor_client.create_user(
        username=req.username,
        display_name=req.display_name,
        phone=req.phone or "",
        email=req.email or "",
        password=req.password,
    )
    logger.info(f"Casdoor user created for sub-account {req.username}: {casdoor_res}")

    # 5. 写入本地 uc_user_profile
    sub_profile = UcUserProfile(
        user_id=new_user_id,
        portal_user_id="",  # 子账号无存量门户ID
        casdoor_user_id=req.username,
        tenant_id=ctx.tenant_id,
        account_type="SUB",
        parent_user_id=parent_profile.user_id if parent_profile else ctx.user_id,
        tree_path=tree_path,
        depth_level=depth_level,
        quota_share_enabled=1,
        status=1,
    )
    db.add(sub_profile)
    db.flush()

    # 6. 工作空间与角色授权
    workspaces = req.workspace_ids
    if not workspaces:
        default_ws = (
            db.query(PermTenantWorkspace)
            .filter_by(tenant_id=ctx.tenant_id, is_default=1)
            .first()
        )
        if default_ws:
            workspaces = [default_ws.workspace_id]

    for ws_id in workspaces:
        binding = PermUserRoleWorkspace(
            tenant_id=ctx.tenant_id,
            user_id=new_user_id,
            workspace_id=ws_id,
            role_code=req.role_code,
            allowed_modules=req.allowed_modules,
        )
        db.add(binding)

    db.commit()
    logger.info(f"Sub-account created: user_id={new_user_id}, path={tree_path}")

    return ResponseModel(
        data=SubAccountCreateResp(
            user_id=new_user_id,
            username=req.username,
            display_name=req.display_name,
            tenant_id=ctx.tenant_id,
            account_type="SUB",
            parent_user_id=sub_profile.parent_user_id,
            tree_path=tree_path,
            depth_level=depth_level,
            workspaces=workspaces,
            role_code=req.role_code,
            allowed_modules=req.allowed_modules,
        )
    )


@router.get(
    "/sub-account/tree",
    response_model=ResponseModel[List[SubAccountItemResp]],
    summary="查询企业主账号下所有子账号列表（物化路径）",
)
def get_sub_account_tree(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    查询当前租户下所有主子账号。
    支持基于 tree_path 呈现清晰的企业组织层级。
    """
    profiles = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.tenant_id == ctx.tenant_id,
            UcUserProfile.is_deleted == 0,
        )
        .order_by(UcUserProfile.depth_level.asc(), UcUserProfile.id.asc())
        .all()
    )

    items: List[SubAccountItemResp] = []
    for p in profiles:
        bindings = (
            db.query(PermUserRoleWorkspace)
            .filter_by(user_id=p.user_id, tenant_id=ctx.tenant_id)
            .all()
        )
        ws_ids = list({b.workspace_id for b in bindings})
        roles = list({b.role_code for b in bindings})
        modules = list({m for b in bindings for m in (b.allowed_modules or [])})

        items.append(
            SubAccountItemResp(
                user_id=p.user_id,
                username=p.casdoor_user_id,
                display_name=p.casdoor_user_id,
                phone=None,
                account_type=p.account_type,
                parent_user_id=p.parent_user_id,
                tree_path=p.tree_path,
                depth_level=p.depth_level,
                status=p.status,
                workspaces=ws_ids,
                roles=roles,
                allowed_modules=modules,
            )
        )

    return ResponseModel(data=items)
