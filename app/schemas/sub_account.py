from typing import List, Optional
from pydantic import BaseModel, Field


class SubAccountCreateReq(BaseModel):
    username: str = Field(..., description="登录名")
    password: str = Field(..., description="登录密码")
    display_name: str = Field(..., description="显示名")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")
    parent_user_id: Optional[str] = Field("", description="直接上级用户ID，为空默认挂在主账号下")
    workspace_ids: List[str] = Field(..., description="授权的工作空间ID列表")
    role_code: str = Field(..., description="租户标准角色编码，如 TENANT_DEVELOPER")
    allowed_modules: List[str] = Field(default_factory=list, description="授权的模块白名单列表")


class SubAccountCreateResp(BaseModel):
    user_id: str
    username: str
    display_name: str
    tenant_id: str
    account_type: str
    parent_user_id: str
    tree_path: str
    depth_level: int
    workspaces: List[str]
    role_code: str
    allowed_modules: List[str]


class WorkspaceBindingItem(BaseModel):
    workspace_id: str = Field(..., description="工作空间ID")
    role_code: str = Field(..., description="该空间下的标准角色编码")
    allowed_modules: List[str] = Field(default_factory=list, description="该空间下的准入模块白名单")


class SubAccountUpdateReq(BaseModel):
    workspace_ids: List[str] = Field(default_factory=list, description="授权的工作空间ID列表")
    role_code: str = Field(..., description="租户标准角色编码，如 TENANT_DEVELOPER")
    allowed_modules: List[str] = Field(default_factory=list, description="授权的模块白名单列表")
    status: Optional[int] = Field(None, description="账号状态: 1-正常, 2-冻结")
    workspace_bindings: Optional[List[WorkspaceBindingItem]] = Field(
        None, description="可选：按工作空间精细化指定角色与模块（传入时优先使用）"
    )


class SubAccountItemResp(BaseModel):
    user_id: str
    username: str
    display_name: str
    phone: Optional[str] = None
    account_type: str
    parent_user_id: str
    tree_path: str
    depth_level: int
    status: int
    workspaces: List[str]
    roles: List[str]
    allowed_modules: List[str]
    workspace_bindings: List[WorkspaceBindingItem] = Field(default_factory=list)
