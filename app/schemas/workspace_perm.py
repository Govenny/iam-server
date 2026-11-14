from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class WorkspaceCreateReq(BaseModel):
    workspace_name: str = Field(..., description="工作空间显示名称")
    k8s_namespace: Optional[str] = Field("", description="映射K8s Namespace")
    model_project_id: Optional[str] = Field("", description="映射模型工程平台项目ID")
    agent_space_id: Optional[str] = Field("", description="映射Agent沙箱ID")
    caict_workspace_id: Optional[str] = Field("", description="映射信通院空间ID")
    quota_config_json: Optional[Dict[str, Any]] = Field(default_factory=dict, description="配额配置")


class WorkspaceResp(BaseModel):
    workspace_id: str
    tenant_id: str
    workspace_name: str
    is_default: bool
    k8s_namespace: str
    model_project_id: str
    agent_space_id: str
    caict_workspace_id: str
    quota_config_json: Dict[str, Any]
    status: int


class ModulesAndRolesResp(BaseModel):
    user_id: str
    tenant_id: str
    workspace_id: str
    role_code: str
    allowed_modules: List[str]
