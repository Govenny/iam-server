"""
E2E Integration Verification for Platform Admin Console & Tenant Console.
"""
import sys
import os
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.casdoor_client import casdoor_client
import app.core.redis_client as redis_module
import app.api.v1.jump_ticket as ticket_mod
import app.api.v1.admin_ops as admin_mod
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace

class MockRedis:
    def __init__(self):
        self.store = {}
        self.lists = {}
    def ping(self):
        return True
    def get(self, k):
        return self.store.get(k)
    def set(self, k, v, ex=None):
        self.store[k] = v
        return True
    def setex(self, k, ttl, v):
        self.store[k] = v
        return True
    def delete(self, k):
        return self.store.pop(k, None) is not None
    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)
        return len(self.lists[k])
    def ltrim(self, k, start, stop):
        if k in self.lists:
            self.lists[k] = self.lists[k][start : stop + 1]
    def lrange(self, k, start, stop):
        lst = self.lists.get(k, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

# Setup mock redis and in-memory sqlite
mock_redis = MockRedis()
redis_module.get_redis_client = lambda: mock_redis
redis_module.check_redis_health = lambda: True
ticket_mod.get_redis_client = lambda: mock_redis
admin_mod.get_redis_client = lambda: mock_redis
casdoor_client.update_user = lambda **kwargs: {"status": "ok"}
casdoor_client.create_user = lambda **kwargs: {"status": "ok"}

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

def run_e2e_verification():
    print(">>> [TEST 1] Testing Normal Tenant SSO exchange & permission boundary...")
    resp_tenant = client.get("/api/v1/sso/exchange-iframe-session?portal_token=token_p_9001_zhangsan")
    assert resp_tenant.status_code == 200, resp_tenant.text
    tenant_data = resp_tenant.json()["data"]
    tenant_token = tenant_data["token"]
    assert tenant_data["role_code"] == "TENANT_ADMIN"
    print(f"  -> Normal Tenant logged in as {tenant_data['username']} ({tenant_data['role_code']})")

    # Normal tenant should be blocked from admin APIs
    r_block = client.get("/api/v1/admin/dashboard/stats", headers={"Authorization": f"Bearer {tenant_token}"})
    assert r_block.status_code == 403, f"Expected 403, got {r_block.status_code}"
    print("  -> Tenant correctly blocked from /admin/* with HTTP 403 Forbidden.")

    print("\n>>> [TEST 2] Testing Platform Admin SSO exchange via token_p_admin_ops...")
    resp_admin = client.get("/api/v1/sso/exchange-iframe-session?portal_token=token_p_admin_ops")
    assert resp_admin.status_code == 200, resp_admin.text
    admin_data = resp_admin.json()["data"]
    admin_token = admin_data["token"]
    assert admin_data["role_code"] == "PLATFORM_ADMIN"
    print(f"  -> Platform Admin logged in as {admin_data['username']} ({admin_data['role_code']})")

    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    print("\n>>> [TEST 3] Admin Tab 1: Dashboard Stats & Radar...")
    r_dash = client.get("/api/v1/admin/dashboard/stats", headers=admin_headers)
    assert r_dash.status_code == 200
    dash_data = r_dash.json()["data"]
    assert "total_tenants" in dash_data
    assert "platform_jumps_breakdown" in dash_data
    assert "health_status" in dash_data
    print(f"  -> Dashboard stats fetched: {dash_data['total_tenants']} tenants, {dash_data['total_users']} users.")

    print("\n>>> [TEST 4] Admin Tab 2: Tenant & Enterprise Management...")
    r_tenants = client.get("/api/v1/admin/tenants", headers=admin_headers)
    assert r_tenants.status_code == 200
    tenants = r_tenants.json()["data"]
    assert len(tenants) > 0
    target_tenant_id = tenants[0]["tenant_id"]
    assert "user_count" in tenants[0], "user_count must be present in tenant item"
    assert "workspace_count" in tenants[0], "workspace_count must be present in tenant item"
    r_up_t = client.put(
        f"/api/v1/admin/tenants/{target_tenant_id}/status",
        headers=admin_headers,
        json={"cert_status": 2, "is_exempt_billing": 1}
    )
    assert r_up_t.status_code == 200
    print(f"  -> Tenant {target_tenant_id} successfully updated with is_exempt_billing=1 and cert_status=2.")

    print("\n>>> [TEST 5] Admin Tab 3: Global Users Management...")
    r_users = client.get("/api/v1/admin/users", headers=admin_headers)
    assert r_users.status_code == 200
    users = r_users.json()["data"]
    assert len(users) > 0
    assert "workspace_count" in users[0], "workspace_count must be present in user item"
    # Pick normal user to update, not platform admin itself
    target_user = next((u for u in users if u["user_id"] != admin_data["user_id"]), users[0])
    target_user_id = target_user["user_id"]
    r_up_u = client.put(
        f"/api/v1/admin/users/{target_user_id}/status",
        headers=admin_headers,
        json={"role_code": "TENANT_DEVELOPER"}
    )
    assert r_up_u.status_code == 200
    print(f"  -> User {target_user_id} successfully updated.")

    print("\n>>> [TEST 6] Admin Tab 4: Jump Ticket Issue & Audit Stream Tracking...")
    r_issue = client.post(
        "/api/v1/auth/jump-ticket/issue",
        headers={"Authorization": f"Bearer {tenant_token}"},
        json={"target_platform": "MODEL_ENGINE", "workspace_id": tenant_data["default_workspace_id"]}
    )
    assert r_issue.status_code == 200
    encrypted_ticket = r_issue.json()["data"]["jump_ticket"]
    print(f"  -> Issued jump ticket ciphertext: {encrypted_ticket[:32]}...")

    r_audit = client.get("/api/v1/admin/audit/tickets?limit=10", headers=admin_headers)
    assert r_audit.status_code == 200, f"Failed to get audit: {r_audit.text}"
    audit_stream = r_audit.json()["data"]
    assert any(item["user_id"] == tenant_data["user_id"] and item["status"] == "ISSUED" for item in audit_stream), "Issued ticket not found in audit stream!"
    print("  -> Audit stream confirmed: Issued ticket successfully captured in Redis stream.")

    print("\n>>> [TEST 7] Admin Tab 5: Data Middle Platform File Sync & Trigger...")
    r_export = client.post("/api/v1/sync/export-data-middle-platform", headers=admin_headers)
    assert r_export.status_code == 200
    r_files = client.get("/api/v1/admin/sync/files", headers=admin_headers)
    assert r_files.status_code == 200
    files_data = r_files.json()["data"]
    assert files_data["user_info_ready"] is True
    assert files_data["user_attr_ready"] is True
    print(f"  -> Offline archive verified: {files_data['user_info_file']} ({files_data['user_info_count']} rows).")

    print("\n>>> [TEST 8] HTML UI Route & Structure Check...")
    r_ui = client.get("/ui")
    assert r_ui.status_code == 200
    assert "平台运营中枢" in r_ui.text
    assert "tab-admin-dashboard" in r_ui.text
    assert "tab-admin-tenants" in r_ui.text
    assert "tab-admin-users" in r_ui.text
    assert "tab-admin-audit" in r_ui.text
    assert "tab-admin-sync" in r_ui.text
    print("  -> /ui page fully verified with all admin console structures.")

    print("\n>>> =================================================")
    print(">>> [ALL E2E CHECKS PASSED] 第三批端到端全链路验收 100% 通过！")
    print(">>> =================================================")

if __name__ == '__main__':
    run_e2e_verification()
