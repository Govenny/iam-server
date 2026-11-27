from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class PortalUserSyncReq(BaseModel):
    portal_user_id: str = Field(..., description="门户系统用户ID")
    username: str = Field(..., description="登录账号名")
    display_name: str = Field(..., description="真实姓名或显示名")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")
    is_platform_admin: bool = Field(False, description="是否平台超级管理员")
    is_enterprise: bool = Field(False, description="是否企业认证账号")
    company_name: Optional[str] = Field("", description="企业名称")


class PortalUserSyncResp(BaseModel):
    user_id: str
    tenant_id: str
    tenant_type: str
    default_workspace_id: str
    is_new_user: bool


class EnterpriseApprovedReq(BaseModel):
    portal_user_id: str = Field(..., description="门户系统用户ID")
    company_name: str = Field(..., description="企业全称")
    credit_code: str = Field(..., description="统一社会信用代码")
    legal_person_name: str = Field(..., description="法人姓名")


class EnterpriseApprovedResp(BaseModel):
    tenant_id: str
    tenant_type: str
    tenant_name: str
    cert_level: int
    cert_status: int


class PortalSubAccountSyncReq(BaseModel):
    portal_user_id: str = Field(..., description="门户系统子账号用户ID（用于幂等及SSO映射）")
    master_portal_user_id: Optional[str] = Field("", description="所属企业主账号在门户的 user_id（与 tenant_id 二选一）")
    tenant_id: Optional[str] = Field("", description="所属企业租户ID（可选，与 master_portal_user_id 二选一）")
    parent_portal_user_id: Optional[str] = Field("", description="直接上级在门户的 user_id（选填，默认挂在主账号下）")
    username: str = Field(..., description="子账号登录名")
    display_name: str = Field(..., description="子账号显示名/员工姓名")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")
    password: Optional[str] = Field(None, description="初始密码（选填，门户管理密码时可留空）")
    workspace_ids: Optional[List[str]] = Field(None, description="初始授权的工作空间ID列表（不传默认授权企业默认空间）")
    role_code: str = Field("TENANT_DEVELOPER", description="初始租户标准角色，默认 TENANT_DEVELOPER")
    allowed_modules: Optional[List[str]] = Field(None, description="研发模块准入白名单，不传默认 ['*']")
    status: int = Field(1, description="账号状态: 1-正常, 2-冻结")


class PortalSubAccountSyncResp(BaseModel):
    user_id: str
    portal_user_id: str
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
    status: int
    is_new_user: bool


class SubLoginReq(BaseModel):
    username: str = Field(..., description="子账号登录名")
    password: str = Field(..., description="密码")


class SubLoginResp(BaseModel):
    user_id: str
    tenant_id: str
    tenant_name: str
    account_type: str
    roles: List[str]
    default_workspace_id: str


class QueryUserInfoReq(BaseModel):
    """国家中试基地数智中台 2.4.3 用户信息查询入参"""
    USER_ID: Optional[str] = Field(None, description="基地入驻用户/机构全局唯一业务编号")
    user_id: Optional[str] = Field(None, description="用户ID")
    portal_user_id: Optional[str] = Field(None, description="门户系统用户ID")
    QUERY_TYPE: Optional[str] = Field("DETAIL", description="查询类型")


class QueryUserInfoResp(BaseModel):
    """国家中试基地数智中台 2.4.3 用户信息查询出参"""
    BIZ_CODE: str = Field("0000", description="业务应答码")
    BIZ_MSG: str = Field("查询成功", description="业务描述")
    USER_ID: str = Field(..., description="基地用户全局唯一标识")
    USER_NAME: str = Field(..., description="用户/机构名称")
    PROVINCE_CODE: str = Field("32", description="归属省份代码 (江苏32)")
    EPARCHY_CODE: str = Field("3205", description="归属地市代码 (苏州3205)")
    STATUS: str = Field("ACTIVE", description="用户状态")


class PortalUserPermContextReq(BaseModel):
    """门户核销 Ticket 时聚合查询用户全量权限上下文请求"""
    portal_user_id: Optional[str] = Field(None, description="门户系统用户ID（与 user_id 二选一）")
    user_id: Optional[str] = Field(None, description="用户中心全局用户ID（与 portal_user_id 二选一）")
    workspace_id: Optional[str] = Field(None, description="指定工作空间ID（选填，不传取默认或首个授权空间）")
    target_platform: Optional[str] = Field(None, description="目标平台编码（选填，传入则按平台过滤模块白名单）")


class AuthorizedWorkspacePermItem(BaseModel):
    """用户有权限的工作空间及其对应角色明细"""
    workspace_id: str
    workspace_name: str
    is_default: bool = False
    k8s_namespace: str = ""
    role_code: str
    allowed_modules: List[str] = Field(default_factory=list)


class PortalUserPermContextResp(BaseModel):
    """门户核销 Ticket 聚合返回的完整用户、租户、空间与角色权限上下文"""
    user_id: str
    portal_user_id: Optional[str] = None
    username: str
    master_user_id: str
    account_type: str
    tenant_id: str
    tenant_name: str
    tenant_type: str
    cert_level: int
    active_workspace_id: str
    active_workspace_name: str
    k8s_namespace: str = ""
    role_code: str
    roles: List[str]
    allowed_modules: List[str]
    has_platform_access: bool = True
    authorized_workspaces: List[AuthorizedWorkspacePermItem] = Field(default_factory=list)
    gateway_headers: Optional[Dict[str, str]] = None
