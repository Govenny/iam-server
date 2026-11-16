from typing import Optional, List
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
