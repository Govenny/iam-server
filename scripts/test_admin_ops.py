import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.casdoor_client import casdoor_client
import app.core.redis_client as redis_module
import app.api.v1.jump_ticket as jt_mod
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

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, val):
        self.store[key] = val
        return True

    def lpush(self, key, val):
        self.lists.setdefault(key, []).insert(0, val)
        return len(self.lists[key])

    def ltrim(self, key, start, stop):
        if key in self.lists:
            self.lists[key] = self.lists[key][start : stop + 1]
        return True

    def lrange(self, key, start, stop):
        lst = self.lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]


mock_redis = MockRedis()
redis_module.get_redis_client = lambda: mock_redis
redis_module.check_redis_health = lambda: True
jt_mod.get_redis_client = lambda: mock_redis
admin_mod.get_redis_client = lambda: mock_redis
casdoor_client.update_user = lambda **kwargs: {"status": "ok"}

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


def seed_data():
    db = TestingSessionLocal()

    # 1. Platform Admin
    db.add(
        UcTenant(
            tenant_id="ten_platform_system",
            tenant_type="ENTERPRISE",
            org_name="org_platform_sys",
            tenant_code="sys_admin",
            tenant_name="数智底座官方运营中心",
            cert_level=2,
            cert_status=2,
        )
    )
    db.add(
        UcUserProfile(
            user_id="usr_plat_admin",
            portal_user_id="portal_admin_001",
            casdoor_user_id="admin_super",
            tenant_id="ten_platform_system",
            account_type="MASTER",
            parent_user_id="",
            tree_path="/usr_plat_admin/",
            depth_level=1,
            status=1,
        )
    )
    db.add(
        PermUserRoleWorkspace(
            tenant_id="ten_platform_system",
            user_id="usr_plat_admin",
            workspace_id="ws_sys_default",
            role_code="PLATFORM_ADMIN",
            allowed_modules=["*"],
        )
    )

    # 2. Enterprise Tenant A (Verified)
    db.add(
        UcTenant(
            tenant_id="ten_ent_a",
            tenant_type="ENTERPRISE",
            org_name="org_ent_a",
            tenant_code="ent_a",
            tenant_name="苏州具身智能研究院",
            cert_level=2,
            cert_status=2,
            credit_code="91320500MA111111AA",
            legal_person_name="李院长",
        )
    )
    db.add(
        PermTenantWorkspace(
            workspace_id="ws_ent_a_01",
            tenant_id="ten_ent_a",
            workspace_name="大模型仿真空间",
            is_default=1,
            status=1,
        )
    )
    db.add(
        UcUserProfile(
            user_id="usr_ent_a_master",
            portal_user_id="p_a_master",
            casdoor_user_id="li_director",
            tenant_id="ten_ent_a",
            account_type="MASTER",
            tree_path="/usr_ent_a_master/",
            depth_level=1,
            status=1,
        )
    )
    db.add(
        PermUserRoleWorkspace(
            tenant_id="ten_ent_a",
            user_id="usr_ent_a_master",
            workspace_id="ws_ent_a_01",
            role_code="TENANT_ADMIN",
            allowed_modules=["*"],
        )
    )

    # 3. Enterprise Tenant B (Pending Verification)
    db.add(
        UcTenant(
            tenant_id="ten_ent_b",
            tenant_type="PERSONAL",
            org_name="org_ent_b",
            tenant_code="ent_b",
            tenant_name="昆山未来物联初创工作室",
            cert_level=0,
            cert_status=1,  # 审核中
            credit_code="91320583MA222222BB",
            legal_person_name="王经理",
        )
    )
    db.add(
        UcUserProfile(
            user_id="usr_ent_b_master",
            portal_user_id="p_b_master",
            casdoor_user_id="wang_dev",
            tenant_id="ten_ent_b",
            account_type="MASTER",
            tree_path="/usr_ent_b_master/",
            depth_level=1,
            status=1,
        )
    )

    db.commit()
    db.close()


def run_tests():
    seed_data()

    h_admin = {
        "X-Portal-User-Id": "usr_plat_admin",
        "X-Portal-Tenant-Id": "ten_platform_system",
    }
    h_tenant = {
        "X-Portal-User-Id": "usr_ent_a_master",
        "X-Portal-Tenant-Id": "ten_ent_a",
    }

    # 1. 权限拦截校验：普通租户访问超管接口必须返回 403
    r_forbid = client.get("/api/v1/admin/dashboard/stats", headers=h_tenant)
    assert r_forbid.status_code == 403, f"Expected 403, got {r_forbid.status_code}"
    print("[PASS] 1. 平台管理权限越权防护校验拦截 (HTTP 403) 通过")

    # 2. 超管获取看板指标统计
    r_stats = client.get("/api/v1/admin/dashboard/stats", headers=h_admin)
    assert r_stats.status_code == 200, r_stats.text
    stats = r_stats.json()["data"]
    assert stats["total_tenants"] >= 3
    assert stats["enterprise_tenants"] >= 2
    assert stats["pending_cert_tenants"] == 1
    assert stats["health_status"]["database"] == "HEALTHY"
    print("[PASS] 2. 平台宏观数据看板与底座健康指标聚合成功:", stats)

    # 3. 超管全量租户列表检索与实名审核操作
    r_tenants = client.get("/api/v1/admin/tenants?keyword=具身智能", headers=h_admin)
    assert r_tenants.status_code == 200
    t_list = r_tenants.json()["data"]
    assert len(t_list) == 1
    assert t_list[0]["tenant_id"] == "ten_ent_a"

    # 运营审核通过 Tenant B，升级为 ENTERPRISE 并开启免计费
    r_audit = client.put(
        "/api/v1/admin/tenants/ten_ent_b/status",
        json={"cert_status": 2, "is_exempt_billing": 1},
        headers=h_admin,
    )
    assert r_audit.status_code == 200, r_audit.text
    b_updated = r_audit.json()["data"]
    assert b_updated["cert_status"] == 2
    assert b_updated["tenant_type"] == "ENTERPRISE"
    assert b_updated["is_exempt_billing"] == 1
    print("[PASS] 3. 租户列表检索、企业实名审核通过与免计费白名单配置成功")

    # 4. 全局跨租户用户检索与角色配置、密码重置
    r_users = client.get("/api/v1/admin/users?keyword=li_director", headers=h_admin)
    assert r_users.status_code == 200
    u_list = r_users.json()["data"]
    assert len(u_list) == 1
    target_uid = u_list[0]["user_id"]

    r_update_u = client.put(
        f"/api/v1/admin/users/{target_uid}/status",
        json={"role_code": "TENANT_ADMIN", "new_password": "NewSecret123!"},
        headers=h_admin,
    )
    assert r_update_u.status_code == 200, r_update_u.text
    assert "TENANT_ADMIN" in r_update_u.json()["data"]["roles"]
    print("[PASS] 4. 全局用户检索、角色配置与密码重置成功")

    # 5. Jump Ticket 审计流水记录
    # 模拟写入一条审计流水
    jt_mod.push_jump_audit_log({
        "ticket_id": "tkt_test_audit_001",
        "user_id": "usr_ent_a_master",
        "username": "li_director",
        "tenant_id": "ten_ent_a",
        "workspace_id": "ws_ent_a_01",
        "target_platform": "MODEL_ENGINE",
        "status": "VERIFIED",
        "issued_at": 1700000000,
        "verified_at": 1700000005,
        "detail": "原子核销成功，已签发 MODEL_ENGINE 网关访问签名",
    })
    r_audit_logs = client.get("/api/v1/admin/audit/tickets?limit=10", headers=h_admin)
    assert r_audit_logs.status_code == 200
    logs = r_audit_logs.json()["data"]
    assert len(logs) >= 1
    assert logs[0]["ticket_id"] == "tkt_test_audit_001"
    print("[PASS] 5. 跨底座免密 Jump Ticket 审计流水查询成功")

    # 6. 中台日文件同步监控
    r_files = client.get("/api/v1/admin/sync/files", headers=h_admin)
    assert r_files.status_code == 200
    file_info = r_files.json()["data"]
    assert "user_info_file" in file_info
    print("[PASS] 6. 国家中试基地中台离线日文件归档监控状态正常")

    print("\n>>> [ALL PASS] 第一批平台超管/运营聚合接口验证通过！")


if __name__ == "__main__":
    run_tests()
