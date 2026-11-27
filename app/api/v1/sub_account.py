import uuid
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.casdoor_client import casdoor_client
from app.core.redis_client import get_redis_client
from app.api.deps import AuthContext, get_current_context
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.sub_account import (
    SubAccountCreateReq,
    SubAccountCreateResp,
    SubAccountUpdateReq,
    SubAccountItemResp,
    WorkspaceBindingItem,
)

logger = logging.getLogger("iam-service.sub_account")

router = APIRouter(prefix="/uc", tags=["企业主子账号树"])

VALID_SUB_ROLES = {
    "TENANT_ADMIN",
    "TENANT_DEVELOPER",
    "TENANT_FINANCE",
    "TENANT_VIEWER",
}
KNOWN_PLATFORMS = ("MODEL_ENGINE", "COMPUTE", "AGENT", "CAICT_DATA_ENG")


def _is_tenant_manager(ctx: AuthContext) -> bool:
    return (
        ctx.account_type == "MASTER"
        or ctx.role_code in ["TENANT_ADMIN", "PLATFORM_ADMIN", "PLATFORM_OPERATOR"]
    )


def _invalidate_user_perm_cache(tenant_id: str, user_id: str, workspace_ids: List[str]) -> None:
    try:
        r = get_redis_client()
        keys = [
            f"perm:user:{tenant_id}:{user_id}:{ws_id}:{plat}"
            for ws_id in set(workspace_ids)
            for plat in KNOWN_PLATFORMS
        ]
        if keys and hasattr(r, "delete"):
            r.delete(*keys)
    except Exception as e:
        logger.warning(f"Failed to invalidate permission cache for user {user_id}: {e}")


@router.post(
    "/sub-account/create",
    response_model=ResponseModel[SubAccountCreateResp],
    summary="[已收敛] 创建企业子账号（内部测试/兼容接口，页面入口已关闭，由门户审核后同步）",
    deprecated=True,
)
def create_sub_account(
    req: SubAccountCreateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    [内部兼容接口]
    注意：当前用户权限中心管理界面不开放直接新建子账号入口。
    生产环境下所有子账号由企业在对外运营服务平台提报审批后，调用 POST /api/v1/sync/sub-account 统一同步建档。
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

    if not _is_tenant_manager(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有主账号或企业管理员（TENANT_ADMIN）有权创建子账号",
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
        ws_ids = list(dict.fromkeys(b.workspace_id for b in bindings))
        roles = list(dict.fromkeys(b.role_code for b in bindings))
        modules = list(dict.fromkeys(m for b in bindings for m in (b.allowed_modules or [])))
        ws_bindings = [
            WorkspaceBindingItem(
                workspace_id=b.workspace_id,
                role_code=b.role_code,
                allowed_modules=b.allowed_modules or [],
            )
            for b in bindings
        ]

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
                workspace_bindings=ws_bindings,
            )
        )

    return ResponseModel(data=items)


@router.put(
    "/sub-account/{user_id}/permissions",
    response_model=ResponseModel[SubAccountItemResp],
    summary="主账号/企业管理员编辑子账号的角色、模块权限与工作空间",
)
def update_sub_account_permissions(
    user_id: str,
    req: SubAccountUpdateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    由企业主账号或企业管理员编辑指定子账号的授权工作空间、标准角色、模块准入白名单及账号状态。
    """
    if not _is_tenant_manager(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有主账号或企业管理员有权编辑子账号权限",
        )

    is_platform_staff = ctx.role_code in ["PLATFORM_ADMIN", "PLATFORM_OPERATOR"]
    user_query = db.query(UcUserProfile).filter(
        UcUserProfile.user_id == user_id,
        UcUserProfile.is_deleted == 0,
    )
    if not is_platform_staff:
        user_query = user_query.filter(UcUserProfile.tenant_id == ctx.tenant_id)
    profile = user_query.first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到当前企业下的用户 {user_id}",
        )
    target_tenant_id = profile.tenant_id

    if profile.account_type == "MASTER":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="企业主账号不支持通过子账号权限接口修改",
        )

    # 构造最终绑定列表
    desired_bindings: List[WorkspaceBindingItem] = []
    if req.workspace_bindings is not None:
        seen_ws = set()
        for item in req.workspace_bindings:
            if item.role_code not in VALID_SUB_ROLES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"无效的角色编码: {item.role_code}",
                )
            if item.workspace_id in seen_ws:
                continue
            seen_ws.add(item.workspace_id)
            mods = item.allowed_modules if item.role_code == "TENANT_DEVELOPER" else ["*"]
            desired_bindings.append(
                WorkspaceBindingItem(
                    workspace_id=item.workspace_id,
                    role_code=item.role_code,
                    allowed_modules=mods,
                )
            )
    else:
        if req.role_code not in VALID_SUB_ROLES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无效的角色编码: {req.role_code}",
            )
        mods = req.allowed_modules if req.role_code == "TENANT_DEVELOPER" else ["*"]
        for ws_id in dict.fromkeys(req.workspace_ids):
            desired_bindings.append(
                WorkspaceBindingItem(
                    workspace_id=ws_id,
                    role_code=req.role_code,
                    allowed_modules=mods,
                )
            )

    # 校验所有目标工作空间均属于目标企业且活跃
    target_ws_ids = [b.workspace_id for b in desired_bindings]
    if target_ws_ids:
        valid_workspaces = (
            db.query(PermTenantWorkspace.workspace_id)
            .filter(
                PermTenantWorkspace.tenant_id == target_tenant_id,
                PermTenantWorkspace.workspace_id.in_(target_ws_ids),
                PermTenantWorkspace.status == 1,
            )
            .all()
        )
        valid_ws_set = {row[0] for row in valid_workspaces}
        invalid_ws = [ws_id for ws_id in target_ws_ids if ws_id not in valid_ws_set]
        if invalid_ws:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"工作空间不存在或不属于目标企业: {', '.join(invalid_ws)}",
            )

    # 查询旧绑定用于缓存失效
    old_bindings = (
        db.query(PermUserRoleWorkspace)
        .filter_by(tenant_id=target_tenant_id, user_id=user_id)
        .all()
    )
    affected_ws_ids = [b.workspace_id for b in old_bindings] + target_ws_ids

    # 替换绑定（显式删除并 flush，避免 SQLAlchemy identity map 残留与唯一键冲突）
    for ob in old_bindings:
        db.delete(ob)
    db.flush()

    for b in desired_bindings:
        db.add(
            PermUserRoleWorkspace(
                tenant_id=target_tenant_id,
                user_id=user_id,
                workspace_id=b.workspace_id,
                role_code=b.role_code,
                allowed_modules=b.allowed_modules,
            )
        )

    db.commit()
    _invalidate_user_perm_cache(target_tenant_id, user_id, affected_ws_ids)
    logger.info(
        f"Updated sub-account permissions: user_id={user_id}, workspaces={target_ws_ids}, tenant={target_tenant_id}"
    )

    ws_ids = [b.workspace_id for b in desired_bindings]
    roles = list(dict.fromkeys(b.role_code for b in desired_bindings))
    modules = list(dict.fromkeys(m for b in desired_bindings for m in (b.allowed_modules or [])))

    return ResponseModel(
        data=SubAccountItemResp(
            user_id=profile.user_id,
            username=profile.casdoor_user_id,
            display_name=profile.casdoor_user_id,
            phone=None,
            account_type=profile.account_type,
            parent_user_id=profile.parent_user_id,
            tree_path=profile.tree_path,
            depth_level=profile.depth_level,
            status=profile.status,
            workspaces=ws_ids,
            roles=roles,
            allowed_modules=modules,
            workspace_bindings=desired_bindings,
        )
    )
