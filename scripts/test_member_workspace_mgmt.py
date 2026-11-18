import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.casdoor_client import casdoor_client
import app.api.v1.sub_account as sub_mod
import app.api.v1.workspace_perm as ws_mod
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace


class MockRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, val):
        self.store[key] = val
        return True

    def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        return removed


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


mock_redis = MockRedis()
sub_mod.get_redis_client = lambda: mock_redis
ws_mod.get_redis_client = lambda: mock_redis
casdoor_client.create_user = lambda **kwargs: {"status": "ok", "msg": "created"}

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def setup_enterprise():
    db = TestingSessionLocal()
    tenant_id = "ten_ui_001"
    master_uid = "usr_master_ui"
    ws_default = "ws_default_ui"

    db.add(
        UcTenant(
            tenant_id=tenant_id,
            tenant_type="ENTERPRISE",
            org_name="org_ui_001",
            tenant_code="ui_001",
            tenant_name="昆山极简智造有限公司",
            cert_level=2,
            cert_status=2,
            legal_person_name="张总",
        )
    )
    db.add(
        PermTenantWorkspace(
            workspace_id=ws_default,
            tenant_id=tenant_id,
            workspace_name="默认主空间",
            is_default=1,
            k8s_namespace="ns-ui-default",
            quota_config_json={"cpu": "16C", "memory": "64G"},
            status=1,
        )
    )
    db.add(
        UcUserProfile(
            user_id=master_uid,
            portal_user_id="portal_ui_001",
            casdoor_user_id="master_zhang",
            tenant_id=tenant_id,
            account_type="MASTER",
            parent_user_id="",
            tree_path=f"/{master_uid}/",
            depth_level=1,
            quota_share_enabled=1,
            status=1,
        )
    )
    db.add(
        PermUserRoleWorkspace(
            tenant_id=tenant_id,
            user_id=master_uid,
            workspace_id=ws_default,
            role_code="TENANT_ADMIN",
            allowed_modules=["*"],
        )
    )
    db.commit()
    db.close()
    return tenant_id, master_uid, ws_default


def run_tests():
    tenant_id, master_uid, ws_default = setup_enterprise()
    h_master = {"X-Portal-User-Id": master_uid, "X-Portal-Tenant-Id": tenant_id}

    # 1. Create a second workspace & two sub-accounts
    r_ws2 = client.post(
        "/api/v1/perm/workspace/create",
        json={"workspace_name": "具身智能项目空间", "k8s_namespace": "ns-embodied"},
        headers=h_master,
    )
    assert r_ws2.status_code == 200, r_ws2.text
    ws_proj = r_ws2.json()["data"]["workspace_id"]
    assert r_ws2.json()["data"]["member_count"] == 1

    r_sub1 = client.post(
        "/api/v1/uc/sub-account/create",
        json={
            "username": "dev_li",
            "password": "Password123!@#",
            "display_name": "李工",
            "workspace_ids": [ws_default],
            "role_code": "TENANT_DEVELOPER",
            "allowed_modules": ["platform:model_engine:module:*"],
        },
        headers=h_master,
    )
    assert r_sub1.status_code == 200, r_sub1.text
    sub1_uid = r_sub1.json()["data"]["user_id"]
    h_sub1 = {"X-Portal-User-Id": sub1_uid, "X-Portal-Tenant-Id": tenant_id}

    # 2. Sub-account permission editing by master (workspaces, role, modules, status)
    # Seed cache first to verify invalidation
    r_cache = client.get(
        f"/api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id={ws_default}",
        headers=h_sub1,
    )
    assert r_cache.status_code == 200
    cache_key = f"perm:user:{tenant_id}:{sub1_uid}:{ws_default}:MODEL_ENGINE"
    assert cache_key in mock_redis.store

    r_edit = client.put(
        f"/api/v1/uc/sub-account/{sub1_uid}/permissions",
        json={
            "workspace_ids": [ws_default, ws_proj],
            "role_code": "TENANT_DEVELOPER",
            "allowed_modules": [
                "platform:model_engine:module:*",
                "platform:agent:module:*",
            ],
            "status": 2,
        },
        headers=h_master,
    )
    assert r_edit.status_code == 200, r_edit.text
    edited = r_edit.json()["data"]
    assert set(edited["workspaces"]) == {ws_default, ws_proj}
    assert edited["status"] == 2
    assert "platform:agent:module:*" in edited["allowed_modules"]
    assert cache_key not in mock_redis.store, "Redis cache should be invalidated on update"
    print("[PASS] 1. 主账号编辑子账号的工作空间、角色、模块与状态成功，且自动失效缓存")

    # 3. Non-admin cannot edit sub-account; master account cannot be edited via sub-account API
    r_forbid = client.put(
        f"/api/v1/uc/sub-account/{sub1_uid}/permissions",
        json={"workspace_ids": [ws_default], "role_code": "TENANT_ADMIN", "allowed_modules": ["*"]},
        headers=h_sub1,
    )
    assert r_forbid.status_code == 403

    r_master_edit = client.put(
        f"/api/v1/uc/sub-account/{master_uid}/permissions",
        json={"workspace_ids": [ws_default], "role_code": "TENANT_VIEWER", "allowed_modules": []},
        headers=h_master,
    )
    assert r_master_edit.status_code == 400
    print("[PASS] 2. 越权编辑拦截 (403) 与主账号保护 (400) 校验通过")

    # 4. Workspace member list, assign/update, and remove
    r_members = client.get(f"/api/v1/perm/workspace/{ws_proj}/members", headers=h_master)
    assert r_members.status_code == 200, r_members.text
    members = r_members.json()["data"]
    assert len(members) == 2
    assert {m["user_id"] for m in members} == {master_uid, sub1_uid}

    # Update sub1 role in ws_proj to TENANT_FINANCE
    r_assign = client.post(
        f"/api/v1/perm/workspace/{ws_proj}/members",
        json={"user_id": sub1_uid, "role_code": "TENANT_FINANCE", "allowed_modules": []},
        headers=h_master,
    )
    assert r_assign.status_code == 200, r_assign.text
    assert r_assign.json()["data"]["role_code"] == "TENANT_FINANCE"
    assert r_assign.json()["data"]["allowed_modules"] == ["*"]

    # Remove sub1 from ws_proj
    r_del = client.delete(f"/api/v1/perm/workspace/{ws_proj}/members/{sub1_uid}", headers=h_master)
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["data"]["removed"] is True

    # Cannot remove master from workspace
    r_del_master = client.delete(f"/api/v1/perm/workspace/{ws_proj}/members/{master_uid}", headers=h_master)
    assert r_del_master.status_code == 400

    # Verify workspace list member_count reflects removal
    r_ws_list = client.get("/api/v1/perm/workspace/list", headers=h_master)
    assert r_ws_list.status_code == 200
    ws_map = {w["workspace_id"]: w for w in r_ws_list.json()["data"]}
    assert ws_map[ws_proj]["member_count"] == 1
    assert ws_map[ws_default]["member_count"] == 2
    print("[PASS] 3. 工作空间成员查询、角色调整、移除、主账号保护与人数统计全部通过")

    # 5. Portal sub-account approval sync (POST /api/v1/sync/sub-account) + idempotency + SUB SSO
    r_sync_sub = client.post(
        "/api/v1/sync/sub-account",
        json={
            "portal_user_id": "p_sub_ui_002",
            "master_portal_user_id": "portal_ui_001",
            "username": "wang_sync",
            "display_name": "王工(门户同步)",
            "phone": "13900008888",
            "role_code": "TENANT_DEVELOPER",
            "allowed_modules": ["platform:model_engine:module:*"],
        },
    )
    assert r_sync_sub.status_code == 200, r_sync_sub.text
    sync_sub_data = r_sync_sub.json()["data"]
    assert sync_sub_data["is_new_user"] is True
    assert sync_sub_data["account_type"] == "SUB"
    assert sync_sub_data["workspaces"] == [ws_default]
    assert sync_sub_data["parent_user_id"] == master_uid

    # Repeat call must be idempotent
    r_sync_sub2 = client.post(
        "/api/v1/sync/sub-account",
        json={
            "portal_user_id": "p_sub_ui_002",
            "master_portal_user_id": "portal_ui_001",
            "username": "wang_sync",
            "display_name": "王工(门户同步)",
        },
    )
    assert r_sync_sub2.status_code == 200, r_sync_sub2.text
    assert r_sync_sub2.json()["data"]["is_new_user"] is False
    assert r_sync_sub2.json()["data"]["user_id"] == sync_sub_data["user_id"]

    # Synced sub-account SSO exchange should return SUB identity and role
    r_sub_sso = client.get("/api/v1/sso/exchange-iframe-session?portal_token=p_sub_ui_002")
    assert r_sub_sso.status_code == 200, r_sub_sso.text
    sub_sso_data = r_sub_sso.json()["data"]
    assert sub_sso_data["account_type"] == "SUB"
    assert sub_sso_data["role_code"] == "TENANT_DEVELOPER"
    assert sub_sso_data["username"] == "wang_sync"
    print("[PASS] 4. 门户子账号审核通过同步接口 (POST /sync/sub-account) 幂等与 SUB SSO 通过")

    # 6. Verify master iframe session fields & /ui HTML response
    r_sso = client.get("/api/v1/sso/exchange-iframe-session?portal_token=portal_ui_001")
    assert r_sso.status_code == 200, r_sso.text
    sso_data = r_sso.json()["data"]
    assert sso_data["account_type"] == "MASTER"
    assert sso_data["username"] == "master_zhang"
    assert sso_data["tenant_name"] == "昆山极简智造有限公司"

    r_ui = client.get("/ui")
    assert r_ui.status_code == 200
    html = r_ui.text
    for forbidden in [
        "能力商店 API Key",
        "跳转换票",
        "跳往子平台",
        "新建子账号",
        "K8s Namespace",
        "模型工程项目",
        "Agent 沙箱 ID",
        "信通院数据空间 ID",
        "物理配额",
    ]:
        assert forbidden not in html, f"Unexpected text in UI: {forbidden}"
    assert "管理用户" in html
    assert "编辑权限" in html
    print("[PASS] 5. SSO 扩展字段返回与 /ui 精简页面验收通过")

    print("\n>>> 全部新增功能测试 100% 通过 [ALL PASS]！")


if __name__ == "__main__":
    run_tests()
