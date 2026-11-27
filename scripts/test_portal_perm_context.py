"""
Verification test for Option 4: GET/POST /api/v1/sync/user-perm-context
used by the Portal during existing Ticket verification.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def setup_data():
    db = TestingSessionLocal()
    t = UcTenant(
        tenant_id="ten_p_9001",
        org_name="org_9001",
        tenant_code="code_9001",
        tenant_name="昆山数智制造科技有限公司",
        tenant_type="ENTERPRISE",
        cert_level=2,
        cert_status=2,
    )
    db.add(t)

    ws_def = PermTenantWorkspace(
        workspace_id="ws_default_p_9001",
        tenant_id="ten_p_9001",
        workspace_name="默认工作空间",
        is_default=1,
        k8s_namespace="ns-default-9001",
        status=1,
    )
    ws_prj = PermTenantWorkspace(
        workspace_id="ws_prj_vision",
        tenant_id="ten_p_9001",
        workspace_name="视觉大模型项目空间",
        is_default=0,
        k8s_namespace="ns-prj-vision",
        status=1,
    )
    db.add_all([ws_def, ws_prj])

    master = UcUserProfile(
        user_id="usr_p_9001",
        portal_user_id="p_9001",
        casdoor_user_id="zhangsan_ceo",
        tenant_id="ten_p_9001",
        account_type="MASTER",
        tree_path="/usr_p_9001/",
        status=1,
    )
    sub = UcUserProfile(
        user_id="usr_sub_p_sub_01",
        portal_user_id="p_sub_01",
        casdoor_user_id="dev_lisi",
        tenant_id="ten_p_9001",
        account_type="SUB",
        parent_user_id="usr_p_9001",
        tree_path="/usr_p_9001/usr_sub_p_sub_01/",
        depth_level=2,
        status=1,
    )
    db.add_all([master, sub])

    # Sub-account is TENANT_DEVELOPER in default ws (MODEL_ENGINE + COMPUTE), TENANT_VIEWER in ws_prj_vision
    b1 = PermUserRoleWorkspace(
        tenant_id="ten_p_9001",
        user_id="usr_sub_p_sub_01",
        workspace_id="ws_default_p_9001",
        role_code="TENANT_DEVELOPER",
        allowed_modules=["platform:model_engine:module:*", "platform:compute:module:k8s"],
    )
    b2 = PermUserRoleWorkspace(
        tenant_id="ten_p_9001",
        user_id="usr_sub_p_sub_01",
        workspace_id="ws_prj_vision",
        role_code="TENANT_VIEWER",
        allowed_modules=["*"],
    )
    db.add_all([b1, b2])
    db.commit()
    db.close()

setup_data()

def run_tests():
    # 1. GET without workspace_id & target_platform -> returns default workspace + all authorized workspaces + full modules
    r1 = client.get("/api/v1/sync/user-perm-context?portal_user_id=p_sub_01")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()["data"]
    assert d1["user_id"] == "usr_sub_p_sub_01"
    assert d1["master_user_id"] == "usr_p_9001"
    assert d1["active_workspace_id"] == "ws_default_p_9001"
    assert d1["role_code"] == "TENANT_DEVELOPER"
    assert d1["has_platform_access"] is True
    assert len(d1["allowed_modules"]) == 2
    assert len(d1["authorized_workspaces"]) == 2
    assert d1["gateway_headers"]["X-Portal-Master-Uid"] == "usr_p_9001"
    print("[PASS] 1. GET 默认空间及全量模块上下文聚合查询通过")

    # 2. GET with target_platform=MODEL_ENGINE -> filters allowed_modules to model_engine only
    r2 = client.get("/api/v1/sync/user-perm-context?portal_user_id=p_sub_01&target_platform=MODEL_ENGINE")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()["data"]
    assert d2["allowed_modules"] == ["platform:model_engine:module:*"]
    assert d2["has_platform_access"] is True
    print("[PASS] 2. 按目标平台 MODEL_ENGINE 过滤模块白名单成功")

    # 3. GET with target_platform=AGENT (unauthorized platform) -> empty modules & has_platform_access=False
    r3 = client.get("/api/v1/sync/user-perm-context?portal_user_id=p_sub_01&target_platform=AGENT")
    assert r3.status_code == 200, r3.text
    d3 = r3.json()["data"]
    assert d3["allowed_modules"] == []
    assert d3["has_platform_access"] is False
    print("[PASS] 3. 未授权平台 AGENT 自动识别 has_platform_access=False")

    # 4. POST with specific workspace_id=ws_prj_vision -> role switches to TENANT_VIEWER
    r4 = client.post(
        "/api/v1/sync/user-perm-context",
        json={"portal_user_id": "p_sub_01", "workspace_id": "ws_prj_vision"},
    )
    assert r4.status_code == 200, r4.text
    d4 = r4.json()["data"]
    assert d4["active_workspace_id"] == "ws_prj_vision"
    assert d4["role_code"] == "TENANT_VIEWER"
    assert d4["k8s_namespace"] == "ns-prj-vision"
    print("[PASS] 4. POST 指定工作空间切换角色及命名空间返回成功")

    # 5. Unauthorized workspace_id -> 403
    r5 = client.get("/api/v1/sync/user-perm-context?portal_user_id=p_sub_01&workspace_id=ws_other_secret")
    assert r5.status_code == 403
    print("[PASS] 5. 未授权工作空间正确拦截 (HTTP 403)")

    print("\n>>> [ALL PASS] 门户核销聚合上下文接口全部验证通过！")

if __name__ == "__main__":
    run_tests()
