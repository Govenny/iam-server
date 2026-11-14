from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class JumpTicketIssueReq(BaseModel):
    target_platform: str = Field(..., description="目标平台编码，如 MODEL_ENGINE")
    workspace_id: str = Field(..., description="要进入的工作空间ID")


class JumpTicketIssueResp(BaseModel):
    jump_ticket: str
    expires_in: int = 60


class JumpTicketVerifyReq(BaseModel):
    jump_ticket: str = Field(..., description="待核销的单次票据")
    caller_platform: str = Field(..., description="调用核销的子平台编码")


class JumpTicketContextResp(BaseModel):
    tenant_id: str
    tenant_type: str
    cert_level: int
    user_id: str
    master_user_id: str
    account_type: str
    active_workspace_id: str
    roles: List[str]
    allowed_modules: List[str]
    k8s_namespace: str
    gateway_headers: Optional[Dict[str, str]] = None
