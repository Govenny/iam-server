"""
Verification test for Portal 2-step SSO (/api/sso/login -> /api/sso/validateTicket)
and Portal Directory Sync (/api/company/listAuthCompanyList, /api/user/listUserInfoList).
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.portal_client import portal_client
from app.main import app

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


def run_tests():
    # 1. Verify MD5 signature calculation matches 《门户-身份体系接口文档》
    expected_md5 = hashlib.md5(b"test-client4c4a1c7d52").hexdigest()
    assert portal_client.build_auth_sign("test-client") == expected_md5
    print(f"[PASS] 1. MD5 鉴权签名校验一致: {expected_md5}")

    # Configure mock Portal base URL
    orig_base = settings.PORTAL_SSO_BASE_URL
    settings.PORTAL_SSO_BASE_URL = "http://10.213.10.228:7301"

    orig_login = portal_client.sso_login
    orig_validate = portal_client.validate_ticket
    orig_list_comp = portal_client.list_auth_companies
    orig_list_user = portal_client.list_user_info

    try:
        # 2. Test 2-step SSO exchange with valid sessionId
        def mock_sso_login(session_id: str, client_id=None):
            if session_id == "invalid_sess":
                return {"ticket": None, "callbackUrl": "http://10.213.10.228:65/user/nlogin"}
            return {"ticket": "tkt_portal_20s_abc", "callbackUrl": "http://10.213.10.228:8001/ui"}

        def mock_validate_ticket(ticket: str, client_id=None):
            assert ticket == "tkt_portal_20s_abc"
            return {
                "userId": "20088",
                "userName": "ks_ent_user",
                "email": "ks@ent.cn",
                "phone": "13912345678",
                "roleId": 2,
                "roleName": "企业用户",
                "companyId": "cmp_88",
                "companyName": "昆山智能制造有限公司",
                "redirectUrl": "http://10.213.10.228:65/home",
                "token": "portal_live_tok_999",
            }

        portal_client.sso_login = mock_sso_login
        portal_client.validate_ticket = mock_validate_ticket

        r_ok = client.get("/api/v1/sso/exchange-iframe-session?sessionId=valid_sess_123")
        assert r_ok.status_code == 200, r_ok.text
        d_ok = r_ok.json()["data"]
        assert d_ok["username"] == "ks_ent_user"
        assert d_ok["tenant_type"] == "ENTERPRISE"
        assert d_ok["tenant_name"] == "昆山智能制造有限公司"
        assert d_ok["portal_token"] == "portal_live_tok_999"
        assert d_ok["redirect_url"] == "http://10.213.10.228:65/home"
        print("[PASS] 2. iframe 传入 sessionId 两步换票 (/api/sso/login -> /api/sso/validateTicket) 成功")

        # 3. Test unauthenticated sessionId -> HTTP 401 with login_url
        r_unauth = client.get("/api/v1/sso/exchange-iframe-session?sessionId=invalid_sess")
        assert r_unauth.status_code == 401, r_unauth.text
        detail = r_unauth.json()["detail"]
        assert detail["login_url"] == "http://10.213.10.228:65/user/nlogin"
        print("[PASS] 3. 门户未登录 (ticket=null) 准确返回 HTTP 401 与统一登录页 login_url")

        # 4. Test dev mock token still works even when PORTAL_SSO_BASE_URL is configured
        r_dev = client.get("/api/v1/sso/exchange-iframe-session?portal_token=token_p_admin_ops")
        assert r_dev.status_code == 200, r_dev.text
        assert r_dev.json()["data"]["role_code"] == "PLATFORM_ADMIN"
        print("[PASS] 4. 本地调试凭证 token_p_admin_ops 兼容模式正常工作")

        # 5. Test directory pull (/api/v1/sync/pull-from-portal)
        portal_client.list_auth_companies = lambda client_id=None: [
            {"companyCode": "Xt903782", "companyName": "昆山示范基地企业"}
        ]
        portal_client.list_user_info = lambda client_id=None: [
            {
                "userName": "dir_user_01",
                "email": "dir@ent.cn",
                "mobile": "13800001111",
                "roleId": 2,
                "roleName": "普通成员",
                "companyCode": "Xt903782",
                "companyName": "昆山示范基地企业",
            }
        ]
        r_pull = client.post("/api/v1/sync/pull-from-portal")
        assert r_pull.status_code == 200, r_pull.text
        d_pull = r_pull.json()["data"]
        assert d_pull["fetched_companies"] == 1
        assert d_pull["new_companies_synced"] == 1
        assert d_pull["fetched_users"] == 1
        assert d_pull["new_users_synced"] == 1
        print("[PASS] 5. 全量拉取门户企业与用户目录同步 (/sync/pull-from-portal) 成功")

        print("\n>>> [ALL PASS] 门户统一身份认证体系对接测试全部通过！")
    finally:
        settings.PORTAL_SSO_BASE_URL = orig_base
        portal_client.sso_login = orig_login
        portal_client.validate_ticket = orig_validate
        portal_client.list_auth_companies = orig_list_comp
        portal_client.list_user_info = orig_list_user


if __name__ == "__main__":
    run_tests()
