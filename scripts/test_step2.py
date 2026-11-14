import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.main import app
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile
from app.models.workspace import PermTenantWorkspace
from app.models.role_binding import PermUserRoleWorkspace

from sqlalchemy.pool import StaticPool

# Create an in-memory SQLite engine for testing Step 2 logic
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Create all tables in SQLite
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def run_step2_tests():
    print("==================================================")
    print(">>> 开始执行 Step 2: 门户旁路同步与企业认证全链路测试")
    print("==================================================")

    # 1. 测试个人用户同步 (sync/user)
    print("\n[用例 1] 门户个人游客首次同步 (POST /api/v1/sync/user)...")
    req_data = {
        "portal_user_id": "p_1001",
        "username": "zhangsan",
        "display_name": "张三",
        "phone": "13800138000",
        "is_platform_admin": False,
        "is_enterprise": False,
        "company_name": "",
    }
    res = client.post("/api/v1/sync/user", json=req_data)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    body = res.json()
    assert body["code"] == 200, f"Expected code 200, got {body}"
    data = body["data"]
    print(f"  -> 同步结果: user_id={data['user_id']}, tenant_id={data['tenant_id']}, tenant_type={data['tenant_type']}, is_new={data['is_new_user']}")
    assert data["user_id"] == "usr_p_1001"
    assert data["tenant_id"] == "ten_p_1001"
    assert data["tenant_type"] == "PERSONAL"
    assert data["is_new_user"] is True
    print("  [PASS] 首次个人用户同步成功，自动创建 PERSONAL 租户与默认空间！")

    # 2. 幂等性测试 (再次同步相同用户)
    print("\n[用例 2] 幂等性测试: 相同用户再次同步...")
    res2 = client.post("/api/v1/sync/user", json=req_data)
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["data"]["is_new_user"] is False
    assert body2["data"]["user_id"] == "usr_p_1001"
    print("  [PASS] 幂等校验通过，已存在用户正确返回已绑定信息！")

    # 3. 企业认证审核通过回调 (sync/enterprise-approved)
    print("\n[用例 3] 门户企业认证审核通过回调 (POST /api/v1/sync/enterprise-approved)...")
    approve_data = {
        "portal_user_id": "p_1001",
        "company_name": "江苏昆山数智创研有限公司",
        "credit_code": "91320583MA1XXXXX99",
        "legal_person_name": "张三",
    }
    res_app = client.post("/api/v1/sync/enterprise-approved", json=approve_data)
    assert res_app.status_code == 200, f"Expected 200, got {res_app.status_code}: {res_app.text}"
    app_body = res_app.json()
    app_data = app_body["data"]
    print(f"  -> 企业升级结果: tenant_id={app_data['tenant_id']}, tenant_type={app_data['tenant_type']}, cert_level={app_data['cert_level']}, tenant_name={app_data['tenant_name']}")
    assert app_data["tenant_type"] == "ENTERPRISE"
    assert app_data["cert_level"] == 2
    assert app_data["cert_status"] == 2
    assert app_data["tenant_name"] == "江苏昆山数智创研有限公司"
    print("  [PASS] 租户成功升级为 ENTERPRISE 企业实名，解锁子账号能力！")

    # 4. iframe 免密换票与 JIT 懒加载测试 (exchange-iframe-session)
    print("\n[用例 4] iframe 嵌入换票与 JIT 懒加载 (GET /api/v1/sso/exchange-iframe-session)...")
    res_sso = client.get("/api/v1/sso/exchange-iframe-session?portal_token=token_p_2002_lisi")
    assert res_sso.status_code == 200, f"Expected 200, got {res_sso.status_code}: {res_sso.text}"
    sso_data = res_sso.json()["data"]
    print(f"  -> JIT 换票成功: user_id={sso_data['user_id']}, token={sso_data['token'][:25]}...")
    assert sso_data["user_id"] == "usr_p_2002"
    assert "token" in sso_data
    print("  [PASS] JIT 懒加载建档并签发 8001 内部 JWT 访问令牌成功！")

    # 5. 企业子账号登录门户兜底接口测试 (sub-login)
    print("\n[用例 5] 企业子账号登录门户兜底 (POST /api/v1/sso/sub-login)...")
    # 先在数据库中构造一个企业子账号
    db = TestingSessionLocal()
    sub_profile = UcUserProfile(
        user_id="usr_sub_lisi_dev",
        portal_user_id="",  # 子账号没有 portal_user_id
        casdoor_user_id="algo_dev_lisi",
        tenant_id="ten_p_1001",
        account_type="SUB",
        parent_user_id="usr_p_1001",
        tree_path="/usr_p_1001/usr_sub_lisi_dev/",
        depth_level=2,
        quota_share_enabled=1,
        status=1,
    )
    db.add(sub_profile)
    sub_role = PermUserRoleWorkspace(
        tenant_id="ten_p_1001",
        user_id="usr_sub_lisi_dev",
        workspace_id="ws_default_p_1001",
        role_code="TENANT_DEVELOPER",
        allowed_modules=["platform:model_engine:module:*"],
    )
    db.add(sub_role)
    db.commit()
    db.close()

    # 模拟子账号登录 (单元测试中 mock Casdoor 验证为通过)
    from app.core.casdoor_client import casdoor_client
    casdoor_client.verify_password = lambda username, password: True

    sub_login_req = {
        "username": "algo_dev_lisi",
        "password": "Password123!@#",
    }
    res_sub = client.post("/api/v1/sso/sub-login", json=sub_login_req)
    assert res_sub.status_code == 200, f"Expected 200, got {res_sub.status_code}: {res_sub.text}"
    sub_data = res_sub.json()["data"]
    print(f"  -> 子账号登录响应: user_id={sub_data['user_id']}, tenant_id={sub_data['tenant_id']}, tenant_name={sub_data['tenant_name']}, account_type={sub_data['account_type']}, roles={sub_data['roles']}")
    assert sub_data["user_id"] == "usr_sub_lisi_dev"
    assert sub_data["account_type"] == "SUB"
    assert "TENANT_DEVELOPER" in sub_data["roles"]
    assert sub_data["tenant_name"] == "江苏昆山数智创研有限公司"
    print("  [PASS] 企业子账号 sub-login 登录兜底验证通过！")

    print("\n==================================================")
    print(">>> Step 2 全部 5 项业务逻辑断言 100% 通过 [ALL PASS]！")
    print("==================================================")


if __name__ == "__main__":
    try:
        run_step2_tests()
    except Exception as e:
        print(f"\n[FAIL] Step 2 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
