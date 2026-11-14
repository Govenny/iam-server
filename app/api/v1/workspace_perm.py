import uuid
import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import get_redis_client
from app.api.deps import AuthContext, get_current_context
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace
from app.schemas.common import ResponseModel
from app.schemas.workspace_perm import (
    WorkspaceCreateReq,
    WorkspaceResp,
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
    if ctx.role_code not in ["TENANT_ADMIN", "PLATFORM_ADMIN"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有企业管理员有权新建工作空间",
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
        )
        for w in workspaces
    ]
    return ResponseModel(data=items)


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
