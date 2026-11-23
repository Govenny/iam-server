"""
Verification test for Platform Admin cross-tenant user/workspace governance and no-downgrade guarantee.
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
from app.core.security import create_internal_jwt

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

    # 1. Platform Admin Tenant & Profile
    tenant_admin = UcTenant(
        tenant_id="ten_platform_system",
        org_name="org_platform",
        tenant_code="code_platform",
        tenant_name="数智中台运营中心",
        tenant_type="ENTERPRISE",
        cert_status=1,
    )
    db.add(tenant_admin)

    user_admin = UcUserProfile(
        user_id="usr_admin_ops",
        portal_user_id="p_admin_ops",
        casdoor_user_id="admin_ops",
        tenant_id="ten_platform_system",
        account_type="MASTER",
        tree_path="/usr_admin_ops/",
        status=1,
    )
    db.add(user_admin)

    # Admin's role binding in system workspace
    binding_admin = PermUserRoleWorkspace(
        tenant_id="ten_platform_system",
        user_id="usr_admin_ops",
        workspace_id="ws_sys_default",
        role_code="PLATFORM_ADMIN",
        allowed_modules=["*"],
    )
    db.add(binding_admin)

    # 2. Enterprise Tenant & Users
    tenant_ent = UcTenant(
        tenant_id="ten_target_company",
        org_name="org_target",
        tenant_code="code_target",
        tenant_name="昆山某智能制造企业",
        tenant_type="ENTERPRISE",
        cert_status=1,
    )
    db.add(tenant_ent)

    ws_ent_1 = PermTenantWorkspace(
        workspace_id="ws_ent_default",
        tenant_id="ten_target_company",
        workspace_name="企业默认空间",
        is_default=1,
        status=1,
    )
    ws_ent_2 = PermTenantWorkspace(
        workspace_id="ws_ent_prj_a",
        tenant_id="ten_target_company",
        workspace_name="工业视觉项目空间",
        is_default=0,
        status=1,
    )
    db.add_all([ws_ent_1, ws_ent_2])

    user_ent_master = UcUserProfile(
        user_id="usr_ent_boss",
        portal_user_id="p_ent_boss",
        casdoor_user_id="ent_boss",
        tenant_id="ten_target_company",
        account_type="MASTER",
        tree_path="/usr_ent_boss/",
        status=1,
    )
    user_ent_sub = UcUserProfile(
        user_id="usr_ent_sub_dev",
        portal_user_id="p_ent_dev",
        casdoor_user_id="ent_dev",
        tenant_id="ten_target_company",
        account_type="SUB",
        parent_user_id="usr_ent_boss",
        tree_path="/usr_ent_boss/usr_ent_sub_dev/",
        status=1,
    )
    db.add_all([user_ent_master, user_ent_sub])

    binding_sub = PermUserRoleWorkspace(
        tenant_id="ten_target_company",
        user_id="usr_ent_sub_dev",
        workspace_id="ws_ent_default",
        role_code="TENANT_DEVELOPER",
        allowed_modules=["platform:model_engine:module:*"],
    )
    db.add(binding_sub)

    db.commit()
    db.close()

setup_data()

# Issue JWT for Platform Admin
token_admin = create_internal_jwt({
    "user_id": "usr_admin_ops",
    "tenant_id": "ten_platform_system",
    "portal_user_id": "p_admin_ops",
    "account_type": "MASTER",
    "role_code": "PLATFORM_ADMIN",
    "workspace_id": "ws_sys_default",
})

headers_admin = {
    "Authorization": f"Bearer {token_admin}",
    "X-Portal-User-Id": "usr_admin_ops",
    "X-Portal-Tenant-Id": "ten_platform_system",
}

def test_admin_edit_sub_account_permissions():
    # Platform Admin edits target company's sub-account permissions
    payload = {
        "workspace_ids": ["ws_ent_default", "ws_ent_prj_a"],
        "role_code": "TENANT_FINANCE",
        "allowed_modules": ["*"],
    }
    r = client.put("/api/v1/uc/sub-account/usr_ent_sub_dev/permissions", json=payload, headers=headers_admin)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()["data"]
    assert "TENANT_FINANCE" in data["roles"]
    assert "ws_ent_prj_a" in data["workspaces"]
    print("[PASS] 1. 平台管理员跨租户编辑子账号权限成功 (未报 403 / 404)！")

def test_admin_manage_workspace_members():
    # Platform Admin assigns / updates member in target company's workspace
    payload = {
        "user_id": "usr_ent_sub_dev",
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:compute:module:*"],
    }
    r = client.post("/api/v1/perm/workspace/ws_ent_prj_a/members", json=payload, headers=headers_admin)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    print("[PASS] 2. 平台管理员跨租户管理工作空间成员成功！")

def test_admin_role_no_downgrade():
    # Even if admin is also added as TENANT_DEVELOPER in a project workspace,
    # their JWT with PLATFORM_ADMIN remains PLATFORM_ADMIN and retains staff privilege
    db = TestingSessionLocal()
    binding_admin_dev = PermUserRoleWorkspace(
        tenant_id="ten_target_company",
        user_id="usr_admin_ops",
        workspace_id="ws_ent_prj_a",
        role_code="TENANT_DEVELOPER",
        allowed_modules=["*"],
    )
    db.add(binding_admin_dev)
    db.commit()
    db.close()

    # Now verify admin endpoints still accept token_admin with 200 OK
    r = client.get("/api/v1/admin/dashboard/stats", headers=headers_admin)
    assert r.status_code == 200, f"Admin was downgraded! Got {r.status_code}: {r.text}"
    print("[PASS] 3. 平台管理员绝不降级覆盖角色保护生效，超管特权未丢失！")

if __name__ == "__main__":
    test_admin_edit_sub_account_permissions()
    test_admin_manage_workspace_members()
    test_admin_role_no_downgrade()
    print("\n>>> [ALL PASS] 跨租户治理与超管防降权回归验证通过！")
