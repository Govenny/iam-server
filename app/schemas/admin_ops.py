from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DashboardStatsResp(BaseModel):
    total_tenants: int = Field(0, description="租户总数")
    enterprise_tenants: int = Field(0, description="企业租户数")
    pending_cert_tenants: int = Field(0, description="待审核企业数")
    total_users: int = Field(0, description="全域用户总数")
    master_users: int = Field(0, description="主账号总数")
    sub_users: int = Field(0, description="子账号总数")
    total_workspaces: int = Field(0, description="纳管工作空间总数")
    today_ticket_jumps: int = Field(0, description="今日底座免密跳传次数")
    platform_jumps_breakdown: Dict[str, int] = Field(
        default_factory=dict, description="底座子平台跳转分布"
    )
    health_status: Dict[str, str] = Field(
        default_factory=dict, description="核心底层组件健康状态"
    )


class AdminTenantItemResp(BaseModel):
    tenant_id: str
    tenant_type: str
    tenant_name: str
    tenant_code: str
    org_name: str
    cert_level: int
    cert_status: int
    credit_code: Optional[str] = None
    legal_person_name: Optional[str] = None
    is_exempt_billing: int
    workspace_count: int = 0
    user_count: int = 0
    master_user_id: Optional[str] = None
    created_at: str


class AdminTenantUpdateReq(BaseModel):
    cert_status: Optional[int] = Field(
        None, description="实名状态: 0-未认证, 1-审核中, 2-已通过, 3-已驳回"
    )
    tenant_type: Optional[str] = Field(None, description="租户类型: PERSONAL / ENTERPRISE")
    is_exempt_billing: Optional[int] = Field(
        None, description="免计费白名单标记: 0-正常计费, 1-免扣费"
    )


class AdminUserItemResp(BaseModel):
    user_id: str
    username: str
    display_name: str
    tenant_id: str
    tenant_name: str
    account_type: str
    depth_level: int
    workspace_count: int = 0
    roles: List[str]
    workspaces: List[str]
    created_at: str


class AdminUserUpdateReq(BaseModel):
    role_code: Optional[str] = Field(None, description="重设角色编码")
    new_password: Optional[str] = Field(None, description="重置密码（可选，同步至 Casdoor）")


class JumpTicketAuditItem(BaseModel):
    ticket_id: str
    user_id: str
    username: Optional[str] = None
    tenant_id: str
    workspace_id: str
    target_platform: str
    status: str  # ISSUED, VERIFIED, REPLAY_BLOCKED, EXPIRED
    issued_at: int
    verified_at: Optional[int] = None
    detail: Optional[str] = None
