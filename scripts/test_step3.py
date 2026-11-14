import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.casdoor_client import casdoor_client
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace

# Setup SQLite test database
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

# Mock casdoor create_user for fast in-memory execution
casdoor_client.create_user = lambda **kwargs: {"status": "ok", "msg": "created"}


def setup_base_enterprise_tenant():
    """初始化一个已企业实名的主账号与租户环境"""
    db = TestingSessionLocal()
    tenant_id = "ten_ent_001"
    user_id = "usr_master_001"
    ws_id = "ws_default_001"

    tenant = UcTenant(
        tenant_id=tenant_id,
        tenant_type="ENTERPRISE",
        org_name="org_ent_001",
        tenant_code="ent_001",
        tenant_name="昆山未来智造科技有限公司",
        cert_level=2,
        cert_status=2,
        credit_code="91320583MA188888XX",
        legal_person_name="李总",
    )
    db.add(tenant)

    workspace = PermTenantWorkspace(
        workspace_id=ws_id,
        tenant_id=tenant_id,
        workspace_name="默认空间",
        is_default=1,
        k8s_namespace="ns-ent-001",
        quota_config_json={"cpu": "16C", "memory": "64G"},
        status=1,
    )
    db.add(workspace)

    profile = UcUserProfile(
        user_id=user_id,
        portal_user_id="portal_master_001",
        casdoor_user_id="master_admin",
        tenant_id=tenant_id,
        account_type="MASTER",
        parent_user_id="",
        tree_path=f"/{user_id}/",
        depth_level=1,
        quota_share_enabled=1,
        status=1,
    )
    db.add(profile)

    role_bind = PermUserRoleWorkspace(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=ws_id,
        role_code="TENANT_ADMIN",
        allowed_modules=["*"],
    )
    db.add(role_bind)

    db.commit()
    db.close()
    return tenant_id, user_id, ws_id


def run_step3_tests():
    print("==================================================")
    print(">>> 开始执行 Step 3: 主子账号树、Workspace与权限隔离测试")
    print("==================================================")

    tenant_id, master_uid, default_ws = setup_base_enterprise_tenant()
    headers_master = {
        "X-Portal-User-Id": master_uid,
        "X-Portal-Tenant-Id": tenant_id,
    }

    # 1. 创建研发子账号 A (模型工程方向)
    print("\n[用例 1] 创建模型工程研发子账号 A (POST /api/v1/uc/sub-account/create)...")
    sub_a_req = {
        "username": "dev_model_alice",
        "password": "Password123!@#",
        "display_name": "爱丽丝(模型专家)",
        "phone": "13911112222",
        "parent_user_id": master_uid,
        "workspace_ids": [default_ws],
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:model_engine:module:*"],
    }
    res_a = client.post("/api/v1/uc/sub-account/create", json=sub_a_req, headers=headers_master)
    assert res_a.status_code == 200, f"Expected 200, got {res_a.status_code}: {res_a.text}"
    data_a = res_a.json()["data"]
    sub_a_uid = data_a["user_id"]
    print(f"  -> 子账号 A 创建成功: user_id={sub_a_uid}, path={data_a['tree_path']}, role={data_a['role_code']}, modules={data_a['allowed_modules']}")
    assert data_a["tree_path"] == f"/{master_uid}/{sub_a_uid}/"
    assert data_a["depth_level"] == 2
    assert "platform:model_engine:module:*" in data_a["allowed_modules"]
    print("  [PASS] 研发子账号 A 物化路径树与模块授权正确！")

    # 2. 创建研发子账号 B (信通院数据工程方向)
    print("\n[用例 2] 创建信通院数据工程子账号 B (POST /api/v1/uc/sub-account/create)...")
    sub_b_req = {
        "username": "dev_data_bob",
        "password": "Password123!@#",
        "display_name": "鲍勃(数据专家)",
        "phone": "13933334444",
        "parent_user_id": master_uid,
        "workspace_ids": [default_ws],
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:caict_data:module:*"],
    }
    res_b = client.post("/api/v1/uc/sub-account/create", json=sub_b_req, headers=headers_master)
    assert res_b.status_code == 200, f"Expected 200, got {res_b.status_code}: {res_b.text}"
    data_b = res_b.json()["data"]
    sub_b_uid = data_b["user_id"]
    print(f"  -> 子账号 B 创建成功: user_id={sub_b_uid}, path={data_b['tree_path']}, role={data_b['role_code']}, modules={data_b['allowed_modules']}")
    assert data_b["tree_path"] == f"/{master_uid}/{sub_b_uid}/"
    assert data_b["depth_level"] == 2
    assert "platform:caict_data:module:*" in data_b["allowed_modules"]
    print("  [PASS] 研发子账号 B 物化路径树与模块授权正确！")

    # 3. 查询主子树列表 (GET /api/v1/uc/sub-account/tree)
    print("\n[用例 3] 查询企业主子树列表 (GET /api/v1/uc/sub-account/tree)...")
    res_tree = client.get("/api/v1/uc/sub-account/tree", headers=headers_master)
    assert res_tree.status_code == 200, f"Expected 200, got {res_tree.status_code}: {res_tree.text}"
    tree_items = res_tree.json()["data"]
    print(f"  -> 主子树总账号数: {len(tree_items)}")
    for item in tree_items:
        print(f"     * [{item['account_type']}] {item['username']} ({item['user_id']}) - 深度: {item['depth_level']}, 空间: {item['workspaces']}, 角色: {item['roles']}")
    assert len(tree_items) >= 3, "Expected at least 1 master and 2 sub-accounts"
    user_ids = [item["user_id"] for item in tree_items]
    assert master_uid in user_ids
    assert sub_a_uid in user_ids
    assert sub_b_uid in user_ids
    print("  [PASS] 主子账号物化树结构完整，深度与层级关系无误！")

    # 4. 工作空间创建与查询 (perm/workspace)
    print("\n[用例 4] 新建项目工作空间并查询列表 (POST/GET /api/v1/perm/workspace)...")
    ws_new_req = {
        "workspace_name": "自动驾驶预研空间",
        "k8s_namespace": "ns-autonomous-drive",
        "model_project_id": "prj-ad-2026",
        "agent_space_id": "sbx-ad-01",
        "quota_config_json": {"gpu": "4*A100", "memory": "128G"},
    }
    res_ws_create = client.post("/api/v1/perm/workspace/create", json=ws_new_req, headers=headers_master)
    assert res_ws_create.status_code == 200, f"Expected 200: {res_ws_create.text}"
    new_ws_id = res_ws_create.json()["data"]["workspace_id"]
    print(f"  -> 新空间已创建: workspace_id={new_ws_id}, name={ws_new_req['workspace_name']}")

    res_ws_list = client.get("/api/v1/perm/workspace/list", headers=headers_master)
    assert res_ws_list.status_code == 200
    ws_list = res_ws_list.json()["data"]
    print(f"  -> 当前租户空间列表数: {len(ws_list)}")
    assert len(ws_list) >= 2, "Expected at least 2 workspaces (default + new project)"
    print("  [PASS] 工作空间新建及租户空间隔离查询通过！")

    # 5. 权限与模块隔离查询 (GET /api/v1/perm/user/modules-and-roles)
    print("\n[用例 5] 跨账号平台模块准入隔离校验 (GET /api/v1/perm/user/modules-and-roles)...")
    # 子账号 A 在 MODEL_ENGINE 平台的权限
    headers_a = {"X-Portal-User-Id": sub_a_uid, "X-Portal-Tenant-Id": tenant_id}
    res_perm_a = client.get(
        f"/api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id={default_ws}",
        headers=headers_a,
    )
    assert res_perm_a.status_code == 200
    perm_a_data = res_perm_a.json()["data"]
    print(f"  -> 子账号 A (模型方向) 访问 MODEL_ENGINE 准入模块: {perm_a_data['allowed_modules']}")
    assert "platform:model_engine:module:*" in perm_a_data["allowed_modules"]

    # 子账号 B 在 MODEL_ENGINE 平台的权限（应该被过滤隔离）
    headers_b = {"X-Portal-User-Id": sub_b_uid, "X-Portal-Tenant-Id": tenant_id}
    res_perm_b = client.get(
        f"/api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id={default_ws}",
        headers=headers_b,
    )
    assert res_perm_b.status_code == 200
    perm_b_data = res_perm_b.json()["data"]
    print(f"  -> 子账号 B (数据方向) 访问 MODEL_ENGINE 准入模块: {perm_b_data['allowed_modules']}")
    assert "platform:model_engine:module:*" not in perm_b_data["allowed_modules"]
    assert len(perm_b_data["allowed_modules"]) == 0, "Expected empty modules for ungranted platform"
    print("  [PASS] 研发子账号 A 与 B 的平台大模块准入权限严格隔离！")

    print("\n==================================================")
    print(">>> Step 3 全部测试用例 100% 通过 [ALL PASS]！")
    print("==================================================")


if __name__ == "__main__":
    try:
        run_step3_tests()
    except Exception as e:
        print(f"\n[FAIL] Step 3 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
