import os
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
import app.core.redis_client as redis_module
from app.core.security import (
    verify_gateway_signature,
    generate_gateway_signature,
    generate_gateway_headers,
)
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace

# Setup In-Memory SQLite
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


# Mock Redis for atomic getdel and setex testing
class MockRedis:
    def __init__(self):
        self.data = {}
        self.exp = {}

    def setex(self, name, time_sec, value):
        self.data[name] = value
        self.exp[name] = time.time() + time_sec
        return True

    def getdel(self, name):
        if name in self.data:
            if time.time() <= self.exp.get(name, float("inf")):
                val = self.data.pop(name)
                self.exp.pop(name, None)
                return val
            else:
                self.data.pop(name, None)
                self.exp.pop(name, None)
        return None

    def get(self, name):
        if name in self.data and time.time() <= self.exp.get(name, float("inf")):
            return self.data[name]
        return None


mock_redis_instance = MockRedis()
redis_module.get_redis_client = lambda: mock_redis_instance

import app.api.v1.jump_ticket as jt
jt.get_redis_client = lambda: mock_redis_instance


def setup_test_environment():
    """初始化用于 Jump Ticket 测试的企业主子账号及工作空间"""
    db = TestingSessionLocal()
    tenant_id = "ten_tkt_001"
    master_uid = "usr_master_tkt"
    sub_uid = "usr_sub_tkt_01"
    ws_id = "ws_tkt_001"

    tenant = UcTenant(
        tenant_id=tenant_id,
        tenant_type="ENTERPRISE",
        org_name="org_tkt_001",
        tenant_code="tkt_001",
        tenant_name="苏州工业数智联合实验室",
        cert_level=2,
        cert_status=2,
        credit_code="91320500MA200000XX",
        legal_person_name="王博士",
    )
    db.add(tenant)

    workspace = PermTenantWorkspace(
        workspace_id=ws_id,
        tenant_id=tenant_id,
        workspace_name="大模型预训练专属空间",
        is_default=1,
        k8s_namespace="ns-large-model-01",
        quota_config_json={"gpu": "8*H800", "memory": "256G"},
        status=1,
    )
    db.add(workspace)

    master_profile = UcUserProfile(
        user_id=master_uid,
        portal_user_id="portal_master_tkt",
        casdoor_user_id="master_wang",
        tenant_id=tenant_id,
        account_type="MASTER",
        parent_user_id="",
        tree_path=f"/{master_uid}/",
        depth_level=1,
        quota_share_enabled=1,
        status=1,
    )
    db.add(master_profile)

    sub_profile = UcUserProfile(
        user_id=sub_uid,
        portal_user_id="",
        casdoor_user_id="dev_sub_zhao",
        tenant_id=tenant_id,
        account_type="SUB",
        parent_user_id=master_uid,
        tree_path=f"/{master_uid}/{sub_uid}/",
        depth_level=2,
        quota_share_enabled=1,
        status=1,
    )
    db.add(sub_profile)

    role_bind = PermUserRoleWorkspace(
        tenant_id=tenant_id,
        user_id=sub_uid,
        workspace_id=ws_id,
        role_code="TENANT_DEVELOPER",
        allowed_modules=["platform:model_engine:module:*"],
    )
    db.add(role_bind)

    db.commit()
    db.close()
    return tenant_id, master_uid, sub_uid, ws_id


def run_step4_tests():
    print("==================================================")
    print(">>> 开始执行 Step 4: 60s 免密 Jump Ticket 与网关签名测试")
    print("==================================================")

    tenant_id, master_uid, sub_uid, ws_id = setup_test_environment()
    headers_sub = {
        "X-Portal-User-Id": sub_uid,
        "X-Portal-Tenant-Id": tenant_id,
    }

    # 1. 签发 Jump Ticket (POST /api/v1/auth/jump-ticket/issue)
    print("\n[用例 1] 签发 60s 跨平台免密跳转换票 (POST /api/v1/auth/jump-ticket/issue)...")
    issue_req = {
        "target_platform": "MODEL_ENGINE",
        "workspace_id": ws_id,
    }
    res_issue = client.post("/api/v1/auth/jump-ticket/issue", json=issue_req, headers=headers_sub)
    assert res_issue.status_code == 200, f"Expected 200: {res_issue.text}"
    issue_data = res_issue.json()["data"]
    ticket = issue_data["jump_ticket"]
    expires_in = issue_data["expires_in"]
    print(f"  -> 票据签发成功: 密文={ticket[:28]}..., 有效期={expires_in}秒")
    assert expires_in == 60
    assert len(ticket) > 30
    print("  [PASS] 60秒免密 Jump Ticket 签发成功，已写入 Redis 待核销！")

    # 2. 第一次核销：正常核销成功并返回完整上下文
    print("\n[用例 2] 第 1 次原子核销票据 (POST /api/v1/auth/jump-ticket/verify)...")
    verify_req = {
        "jump_ticket": ticket,
        "caller_platform": "MODEL_ENGINE",
    }
    res_v1 = client.post("/api/v1/auth/jump-ticket/verify", json=verify_req)
    assert res_v1.status_code == 200, f"Expected 200: {res_v1.text}"
    ctx_data = res_v1.json()["data"]
    print(f"  -> 第 1 次核销成功: user_id={ctx_data['user_id']}, master_id={ctx_data['master_user_id']}, account_type={ctx_data['account_type']}")
    print(f"     tenant_type={ctx_data['tenant_type']}, k8s_ns={ctx_data['k8s_namespace']}, roles={ctx_data['roles']}")
    assert ctx_data["user_id"] == sub_uid
    assert ctx_data["master_user_id"] == master_uid, "子账号必须正确溯源到顶层主账号"
    assert ctx_data["account_type"] == "SUB"
    assert ctx_data["k8s_namespace"] == "ns-large-model-01"
    assert "TENANT_DEVELOPER" in ctx_data["roles"]
    assert "gateway_headers" in ctx_data
    print("  [PASS] 第 1 次核销成功，上下文解析与主子溯源完整准确！")

    # 3. 第二次核销：防重放拦截（严禁重复使用）
    print("\n[用例 3] 第 2 次重复核销防重放拦截测试...")
    res_v2 = client.post("/api/v1/auth/jump-ticket/verify", json=verify_req)
    print(f"  -> 第 2 次核销响应状态码: {res_v2.status_code}, 内容: {res_v2.text}")
    assert res_v2.status_code == 403, f"Expected 403 Forbidden, got {res_v2.status_code}"
    print("  [PASS] 防重放验证通过！Redis GETDEL 原子保证单次有效，二次核销被严格拦截返回 HTTP 403！")

    # 4. 平台不匹配核销拦截测试
    print("\n[用例 4] 目标平台与调用方平台不匹配拦截测试...")
    # 重新签发一张票据用于测试平台篡改
    res_issue2 = client.post("/api/v1/auth/jump-ticket/issue", json=issue_req, headers=headers_sub)
    ticket2 = res_issue2.json()["data"]["jump_ticket"]
    verify_mismatch_req = {
        "jump_ticket": ticket2,
        "caller_platform": "CAICT_DATA_ENG",  # 故意使用非目标平台调用
    }
    res_mismatch = client.post("/api/v1/auth/jump-ticket/verify", json=verify_mismatch_req)
    print(f"  -> 平台不符核销响应: {res_mismatch.status_code}, 内容: {res_mismatch.text}")
    assert res_mismatch.status_code == 403
    assert "目标平台不符" in res_mismatch.text
    print("  [PASS] 跨平台边界安全校验通过，非授权平台调用被精准拦截！")

    # 5. 网关 HMAC-SHA256 9 大请求头染色与防篡改签名校验
    print("\n[用例 5] 网关 Header 染色与 HMAC-SHA256 签名算法校验...")
    gw_headers = ctx_data["gateway_headers"]
    print("  -> 9 大可信网关请求头生成样例:")
    for k, v in gw_headers.items():
        print(f"     * {k}: {v}")

    # 验证完整签名
    sig_valid = verify_gateway_signature(gw_headers)
    assert sig_valid is True, "Expected gateway signature to be valid"
    print("  -> 网关签名自验证通过: [VALID]")

    # 模拟非法黑客篡改 Header (修改 User-Id)
    tampered_headers = gw_headers.copy()
    tampered_headers["X-Portal-User-Id"] = "hacker_uid_000"
    tampered_valid = verify_gateway_signature(tampered_headers)
    assert tampered_valid is False, "Tampered headers must fail signature verification"
    print("  -> 篡改 Header 后签名验证失败拦截: [CORRECTLY REJECTED]")
    print("  [PASS] 网关 HMAC-SHA256 防篡改与时效签名机制完全正确！")

    print("\n==================================================")
    print(">>> Step 4 全部 5 项测试断言 100% 通过 [ALL PASS]！")
    print("==================================================")


if __name__ == "__main__":
    try:
        run_step4_tests()
    except Exception as e:
        print(f"\n[FAIL] Step 4 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
