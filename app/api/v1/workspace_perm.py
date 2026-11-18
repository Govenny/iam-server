import uuid
import json
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import get_redis_client
from app.api.deps import AuthContext, get_current_context
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.workspace_perm import (
    WorkspaceCreateReq,
    WorkspaceResp,
    WorkspaceMemberResp,
    WorkspaceMemberAssignReq,
    ModulesAndRolesResp,
)

logger = logging.getLogger("iam-service.workspace_perm")

router = APIRouter(prefix="/perm", tags=["工作空间与权限"])

PLATFORM_PREFIX_MAP = {
    "MODEL_ENGINE": "platform:model_engine:",
    "COMPUTE": "platform:compute:",
    "AGENT": "platform:agent:",
    "CAICT_DATA_ENG": "platform:caict_data:",
}
VALID_WORKSPACE_ROLES = {
    "TENANT_ADMIN",
    "TENANT_DEVELOPER",
    "TENANT_FINANCE",
    "TENANT_VIEWER",
    "PLATFORM_ADMIN",
}


def _is_workspace_manager(ctx: AuthContext) -> bool:
    return ctx.account_type == "MASTER" or ctx.role_code in ["TENANT_ADMIN", "PLATFORM_ADMIN"]


def _invalidate_workspace_user_cache(tenant_id: str, user_id: str, workspace_id: str) -> None:
    try:
        r = get_redis_client()
        keys = [
            f"perm:user:{tenant_id}:{user_id}:{workspace_id}:{plat}"
            for plat in PLATFORM_PREFIX_MAP.keys()
        ]
        if keys and hasattr(r, "delete"):
            r.delete(*keys)
    except Exception as e:
        logger.warning(f"Failed to invalidate workspace cache for {user_id}@{workspace_id}: {e}")


@router.post(
    "/workspace/create",
    response_model=ResponseModel[WorkspaceResp],
    summary="新建租户工作空间",
)
def create_workspace(
    req: WorkspaceCreateReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    新建项目工作空间并关联底座基础设施资源（K8s Namespace, Model Project, Agent Space等）。
    自动将创建者绑定为该工作空间的管理角色。
    """
    if not _is_workspace_manager(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有主账号或企业管理员有权新建工作空间",
        )

    workspace_id = f"ws_prj_{uuid.uuid4().hex[:8]}"
    k8s_ns = req.k8s_namespace.strip() if req.k8s_namespace else f"ns-{workspace_id}"
    quota_cfg = req.quota_config_json or {"cpu": "4C", "memory": "16G", "storage": "50G"}

    ws = PermTenantWorkspace(
        workspace_id=workspace_id,
        tenant_id=ctx.tenant_id,
        workspace_name=req.workspace_name,
        is_default=0,
        k8s_namespace=k8s_ns,
        model_project_id=req.model_project_id or "",
        agent_space_id=req.agent_space_id or "",
        caict_workspace_id=req.caict_workspace_id or "",
        quota_config_json=quota_cfg,
        status=1,
    )
    db.add(ws)
    db.flush()

    # 自动将主账号加入新工作空间并赋予管理权限
    admin_binding = PermUserRoleWorkspace(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        workspace_id=workspace_id,
        role_code=ctx.role_code,
        allowed_modules=["*"],
    )
    db.add(admin_binding)

    db.commit()
    logger.info(f"Workspace created: {workspace_id} under tenant {ctx.tenant_id}")

    return ResponseModel(
        data=WorkspaceResp(
            workspace_id=ws.workspace_id,
            tenant_id=ws.tenant_id,
            workspace_name=ws.workspace_name,
            is_default=bool(ws.is_default),
            k8s_namespace=ws.k8s_namespace,
            model_project_id=ws.model_project_id,
            agent_space_id=ws.agent_space_id,
            caict_workspace_id=ws.caict_workspace_id,
            quota_config_json=ws.quota_config_json,
            status=ws.status,
            member_count=1,
        )
    )


@router.get(
    "/workspace/list",
    response_model=ResponseModel[List[WorkspaceResp]],
    summary="查询当前租户的工作空间列表",
)
def list_workspaces(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    查询当前租户下所有活跃工作空间（包含默认主空间与项目空间）。
    """
    workspaces = (
        db.query(PermTenantWorkspace)
        .filter(
            PermTenantWorkspace.tenant_id == ctx.tenant_id,
            PermTenantWorkspace.status == 1,
        )
        .order_by(PermTenantWorkspace.is_default.desc(), PermTenantWorkspace.id.asc())
        .all()
    )

    # 统计每个工作空间的活跃成员数
    count_rows = (
        db.query(
            PermUserRoleWorkspace.workspace_id,
            func.count(func.distinct(PermUserRoleWorkspace.user_id)),
        )
        .join(
            UcUserProfile,
            (UcUserProfile.user_id == PermUserRoleWorkspace.user_id)
            & (UcUserProfile.tenant_id == PermUserRoleWorkspace.tenant_id)
            & (UcUserProfile.is_deleted == 0),
        )
        .filter(PermUserRoleWorkspace.tenant_id == ctx.tenant_id)
        .group_by(PermUserRoleWorkspace.workspace_id)
        .all()
    )
    member_counts = {row[0]: int(row[1]) for row in count_rows}

    items = [
        WorkspaceResp(
            workspace_id=w.workspace_id,
            tenant_id=w.tenant_id,
            workspace_name=w.workspace_name,
            is_default=bool(w.is_default),
            k8s_namespace=w.k8s_namespace,
            model_project_id=w.model_project_id,
            agent_space_id=w.agent_space_id,
            caict_workspace_id=w.caict_workspace_id,
            quota_config_json=w.quota_config_json,
            status=w.status,
            member_count=member_counts.get(w.workspace_id, 0),
        )
        for w in workspaces
    ]
    return ResponseModel(data=items)


@router.get(
    "/workspace/{workspace_id}/members",
    response_model=ResponseModel[List[WorkspaceMemberResp]],
    summary="查询指定工作空间下的成员列表与角色权限",
)
def list_workspace_members(
    workspace_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    ws = (
        db.query(PermTenantWorkspace)
        .filter(
            PermTenantWorkspace.workspace_id == workspace_id,
            PermTenantWorkspace.tenant_id == ctx.tenant_id,
            PermTenantWorkspace.status == 1,
        )
        .first()
    )
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作空间 {workspace_id} 不存在或不可用",
        )

    rows = (
        db.query(PermUserRoleWorkspace, UcUserProfile)
        .join(
            UcUserProfile,
            (UcUserProfile.user_id == PermUserRoleWorkspace.user_id)
            & (UcUserProfile.tenant_id == PermUserRoleWorkspace.tenant_id)
            & (UcUserProfile.is_deleted == 0),
        )
        .filter(
            PermUserRoleWorkspace.tenant_id == ctx.tenant_id,
            PermUserRoleWorkspace.workspace_id == workspace_id,
        )
        .order_by(UcUserProfile.depth_level.asc(), UcUserProfile.id.asc())
        .all()
    )

    members: List[WorkspaceMemberResp] = []
    seen = set()
    for binding, profile in rows:
        if profile.user_id in seen:
            continue
        seen.add(profile.user_id)
        members.append(
            WorkspaceMemberResp(
                user_id=profile.user_id,
                username=profile.casdoor_user_id,
                display_name=profile.casdoor_user_id,
                account_type=profile.account_type,
                status=profile.status,
                role_code=binding.role_code,
                allowed_modules=binding.allowed_modules or [],
            )
        )

    return ResponseModel(data=members)


@router.post(
    "/workspace/{workspace_id}/members",
    response_model=ResponseModel[WorkspaceMemberResp],
    summary="向工作空间添加成员或修改成员角色权限",
)
def assign_workspace_member(
    workspace_id: str,
    req: WorkspaceMemberAssignReq,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    if not _is_workspace_manager(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有主账号或企业管理员有权管理工作空间成员",
        )

    ws = (
        db.query(PermTenantWorkspace)
        .filter(
            PermTenantWorkspace.workspace_id == workspace_id,
            PermTenantWorkspace.tenant_id == ctx.tenant_id,
            PermTenantWorkspace.status == 1,
        )
        .first()
    )
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作空间 {workspace_id} 不存在或不可用",
        )

    profile = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.user_id == req.user_id,
            UcUserProfile.tenant_id == ctx.tenant_id,
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"企业成员 {req.user_id} 不存在",
        )

    if req.role_code not in VALID_WORKSPACE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的角色编码: {req.role_code}",
        )

    if profile.account_type == "MASTER" and req.role_code not in ["TENANT_ADMIN", "PLATFORM_ADMIN"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="企业主账号不可降级为非管理员角色",
        )

    allowed_modules = req.allowed_modules if req.role_code == "TENANT_DEVELOPER" else ["*"]

    # 先清理该用户在此空间的旧角色绑定，保证单空间单主角色清晰一致
    old_ws_bindings = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            tenant_id=ctx.tenant_id,
            user_id=req.user_id,
            workspace_id=workspace_id,
        )
        .all()
    )
    for ob in old_ws_bindings:
        db.delete(ob)
    db.flush()

    db.add(
        PermUserRoleWorkspace(
            tenant_id=ctx.tenant_id,
            user_id=req.user_id,
            workspace_id=workspace_id,
            role_code=req.role_code,
            allowed_modules=allowed_modules,
        )
    )
    db.commit()
    _invalidate_workspace_user_cache(ctx.tenant_id, req.user_id, workspace_id)
    logger.info(
        f"Assigned workspace member: ws={workspace_id}, user={req.user_id}, role={req.role_code}"
    )

    return ResponseModel(
        data=WorkspaceMemberResp(
            user_id=profile.user_id,
            username=profile.casdoor_user_id,
            display_name=profile.casdoor_user_id,
            account_type=profile.account_type,
            status=profile.status,
            role_code=req.role_code,
            allowed_modules=allowed_modules,
        )
    )


@router.delete(
    "/workspace/{workspace_id}/members/{user_id}",
    response_model=ResponseModel[Dict[str, Any]],
    summary="从指定工作空间移除成员",
)
def remove_workspace_member(
    workspace_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    if not _is_workspace_manager(ctx):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有主账号或企业管理员有权移除工作空间成员",
        )

    ws = (
        db.query(PermTenantWorkspace)
        .filter(
            PermTenantWorkspace.workspace_id == workspace_id,
            PermTenantWorkspace.tenant_id == ctx.tenant_id,
            PermTenantWorkspace.status == 1,
        )
        .first()
    )
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作空间 {workspace_id} 不存在或不可用",
        )

    profile = (
        db.query(UcUserProfile)
        .filter(
            UcUserProfile.user_id == user_id,
            UcUserProfile.tenant_id == ctx.tenant_id,
            UcUserProfile.is_deleted == 0,
        )
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"企业成员 {user_id} 不存在",
        )

    if profile.account_type == "MASTER":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="企业主账号不可从工作空间移除",
        )

    deleted_count = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        .delete(synchronize_session="fetch")
    )
    db.commit()
    _invalidate_workspace_user_cache(ctx.tenant_id, user_id, workspace_id)
    logger.info(f"Removed user {user_id} from workspace {workspace_id} (rows={deleted_count})")

    return ResponseModel(
        data={
            "workspace_id": workspace_id,
            "user_id": user_id,
            "removed": deleted_count > 0,
        }
    )


@router.get(
    "/user/modules-and-roles",
    response_model=ResponseModel[ModulesAndRolesResp],
    summary="查询用户在指定空间/平台的角色与模块白名单（Redis优先）",
)
def get_user_modules_and_roles(
    platform_code: str = Query(..., description="目标平台编码，如 MODEL_ENGINE"),
    workspace_id: str = Query(..., description="工作空间ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_current_context),
):
    """
    查询当前用户在指定租户、空间和目标平台下的 7 大标准角色之一与模块准入清单。
    优先读取 Redis 高速缓存（TTL 600秒）。
    """
    cache_key = f"perm:user:{ctx.tenant_id}:{ctx.user_id}:{workspace_id}:{platform_code}"

    # 1. 优先查 Redis 缓存
    try:
        r = get_redis_client()
        cached = r.get(cache_key)
        if cached:
            cached_data = json.loads(cached)
            return ResponseModel(data=ModulesAndRolesResp(**cached_data))
    except Exception as e:
        logger.warning(f"Redis get cache failed: {e}")

    # 2. 查数据库授权绑定
    binding = (
        db.query(PermUserRoleWorkspace)
        .filter_by(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            workspace_id=workspace_id,
        )
        .first()
    )

    if not binding:
        # 若在当前空间无直接绑定，检查是否为超级平台管理员
        if ctx.role_code == "PLATFORM_ADMIN":
            resp_data = ModulesAndRolesResp(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                workspace_id=workspace_id,
                role_code="PLATFORM_ADMIN",
                allowed_modules=["*"],
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"当前用户未被授权访问工作空间 {workspace_id}",
            )
    else:
        role_code = binding.role_code
        modules_raw = binding.allowed_modules or []

        # 平台模块前缀裁剪过滤
        prefix = PLATFORM_PREFIX_MAP.get(platform_code.upper(), "")
        if "*" in modules_raw or "all" in modules_raw:
            filtered_modules = ["*"]
        else:
            filtered_modules = [
                m for m in modules_raw
                if not prefix or m.startswith(prefix) or m.startswith("platform:common:")
            ]

        resp_data = ModulesAndRolesResp(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            workspace_id=workspace_id,
            role_code=role_code,
            allowed_modules=filtered_modules,
        )

    # 3. 写入 Redis 缓存
    try:
        r = get_redis_client()
        r.setex(cache_key, 600, json.dumps(resp_data.model_dump()))
    except Exception as e:
        logger.warning(f"Redis set cache failed: {e}")

    return ResponseModel(data=resp_data)
