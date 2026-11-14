#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数智底座用户与角色权限中心 — 第一目标全链路纯命令行自动化测试脚本 (test_cli.py)
一键串联：
  1. 健康检查与系统就绪
  2. 门户注册/登录静默双写同步 (POST /sync/user 幂等性)
  3. 门户企业认证审核通过回调与租户升级 (POST /sync/enterprise-approved)
  4. iframe 免密换票与 JIT 懒加载 (GET /sso/exchange-iframe-session)
  5. 企业主子账号树创建与物化路径计算 (POST /uc/sub-account/create)
  6. 企业主子账号树查询 (GET /uc/sub-account/tree)
  7. 工作空间生命周期管理 (POST /perm/workspace/create, GET /perm/workspace/list)
  8. 跨平台大模块准入白名单与隔离验证 (GET /perm/user/modules-and-roles)
  9. 企业子账号门户登录兜底认证 (POST /sso/sub-login)
 10. 跨平台 60s 免密 Jump Ticket 签发与原子核销防重放 (POST /auth/jump-ticket/*)
 11. 网关 9 大可信 Header 染色与 HMAC-SHA256 签名算法及防篡改校验
"""

import os
import sys
import time
import json
import uuid

# Ensure project root in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
import app.core.redis_client as redis_module
from app.core.casdoor_client import casdoor_client
from app.core.security import (
    verify_internal_jwt,
    verify_gateway_signature,
    generate_gateway_headers,
)
from app.main import app

# Setup In-Memory SQLite Database
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


# Mock Redis for In-Memory Atomic GETDEL/SETEX
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
import app.api.v1.workspace_perm as wp
wp.get_redis_client = lambda: mock_redis_instance

# Mock Casdoor user creation & verification
casdoor_client.create_user = lambda **kwargs: {"status": "ok", "msg": "created"}
casdoor_client.verify_password = lambda u, p: True


def log_step(step_num: int, title: str):
    print(f"\n======================================================================")
    print(f"  [STEP {step_num}] {title}")
    print(f"======================================================================")


def log_pass(message: str):
    print(f"  [PASS] {message}")


def main():
    print("""
    ######################################################################
    #  数智底座用户与角色权限中心 — 第一目标全链路命令行自动化验收测试  #
    #  Python 3.12 + FastAPI + Casdoor 伴生架构                           #
    ######################################################################
    """)

    # -------------------------------------------------------------
    # Step 1: 健康检查与基础就绪
    # -------------------------------------------------------------
    log_step(1, "伴生服务 (iam-service) 健康检查接口探活")
    res_health = client.get("/health")
    assert res_health.status_code == 200, f"Expected 200, got {res_health.status_code}"
    health_data = res_health.json()
    print(f"  -> 健康探活响应: {health_data}")
    assert health_data.get("status") == "healthy"
    assert health_data.get("service") == "iam-service"
    log_pass("服务健康检查探活通过 (HTTP 200 OK)")

    # -------------------------------------------------------------
    # Step 2: 门户个人用户静默双写同步与幂等性
    # -------------------------------------------------------------
    log_step(2, "存量门户用户静默同步与幂等性验证 (POST /api/v1/sync/user)")
    portal_user_req = {
        "portal_user_id": "p_9001",
        "username": "zhangsan_ceo",
        "display_name": "张三(企业主)",
        "phone": "13812345678",
        "email": "zhangsan@kunshan.ai",
        "is_platform_admin": False,
        "is_enterprise": False,
        "company_name": "",
    }
    # 首次同步
    res_sync1 = client.post("/api/v1/sync/user", json=portal_user_req)
    assert res_sync1.status_code == 200, f"Sync failed: {res_sync1.text}"
    sync1_data = res_sync1.json()["data"]
    master_uid = sync1_data["user_id"]
    tenant_id = sync1_data["tenant_id"]
    default_ws = sync1_data["default_workspace_id"]
    print(f"  -> 首次同步结果: user_id={master_uid}, tenant_id={tenant_id}, ws={default_ws}, type={sync1_data['tenant_type']}, is_new={sync1_data['is_new_user']}")
    assert sync1_data["is_new_user"] is True
    assert sync1_data["tenant_type"] == "PERSONAL"
    log_pass("个人游客首次同步成功，自动创建 PERSONAL 租户与默认主空间")

    # 幂等性再次同步
    res_sync2 = client.post("/api/v1/sync/user", json=portal_user_req)
    assert res_sync2.status_code == 200
    sync2_data = res_sync2.json()["data"]
    assert sync2_data["is_new_user"] is False
    assert sync2_data["user_id"] == master_uid
    log_pass("幂等性校验通过，重复同步精准返回已绑定资产，未重复建号")

    # -------------------------------------------------------------
    # Step 3: 门户企业实名认证审核通过回调
    # -------------------------------------------------------------
    log_step(3, "门户企业实名认证审核通过回调 (POST /api/v1/sync/enterprise-approved)")
    approve_req = {
        "portal_user_id": "p_9001",
        "company_name": "昆山数字智能科技有限公司",
        "credit_code": "91320583MA1TEST001",
        "legal_person_name": "张三",
    }
    res_approve = client.post("/api/v1/sync/enterprise-approved", json=approve_req)
    assert res_approve.status_code == 200, f"Approve failed: {res_approve.text}"
    app_data = res_approve.json()["data"]
    print(f"  -> 企业认证升级: tenant_type={app_data['tenant_type']}, cert_level={app_data['cert_level']}, name={app_data['tenant_name']}")
    assert app_data["tenant_type"] == "ENTERPRISE"
    assert app_data["cert_level"] == 2
    assert app_data["cert_status"] == 2
    log_pass("租户状态成功升级为 ENTERPRISE (cert_level=2)，解锁创建企业子账号权限")

    # -------------------------------------------------------------
    # Step 4: iframe 嵌入免密换票与 JIT 懒加载
    # -------------------------------------------------------------
    log_step(4, "iframe 嵌入页面免密换票与 JIT 懒加载 (GET /api/v1/sso/exchange-iframe-session)")
    res_iframe = client.get("/api/v1/sso/exchange-iframe-session?portal_token=token_p_9002_jit_user")
    assert res_iframe.status_code == 200, f"Iframe session failed: {res_iframe.text}"
    iframe_data = res_iframe.json()["data"]
    jit_token = iframe_data["token"]
    jit_uid = iframe_data["user_id"]
    print(f"  -> JIT 换票成功: user_id={jit_uid}, 内部Token={jit_token[:30]}...")
    assert "token" in iframe_data
    # 校验签发的内部 JWT
    jwt_claims = verify_internal_jwt(jit_token)
    assert jwt_claims is not None, "Internal JWT verification failed"
    assert jwt_claims["user_id"] == jit_uid
    log_pass("JIT 静默建档并签发 8001 内部安全访问 JWT 凭证成功")

    # -------------------------------------------------------------
    # Step 5: 主账号创建子账号与物化路径树
    # -------------------------------------------------------------
    log_step(5, "企业主账号创建多级子账号并写入物化路径树 (POST /api/v1/uc/sub-account/create)")
    headers_master = {
        "X-Portal-User-Id": master_uid,
        "X-Portal-Tenant-Id": tenant_id,
    }

    # 子账号 1: 模型工程研发人员
    sub1_req = {
        "username": "algo_expert_alice",
        "password": "Password123!@#",
        "display_name": "爱丽丝(算法总监)",
        "phone": "13900010001",
        "parent_user_id": master_uid,
        "workspace_ids": [default_ws],
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:model_engine:module:*", "platform:compute:module:k8s"],
    }
    res_sub1 = client.post("/api/v1/uc/sub-account/create", json=sub1_req, headers=headers_master)
    assert res_sub1.status_code == 200, f"Create sub1 failed: {res_sub1.text}"
    sub1_data = res_sub1.json()["data"]
    sub1_uid = sub1_data["user_id"]
    print(f"  -> 子账号 1 已创建: id={sub1_uid}, path={sub1_data['tree_path']}, depth={sub1_data['depth_level']}, role={sub1_data['role_code']}")
    assert sub1_data["tree_path"] == f"/{master_uid}/{sub1_uid}/"
    assert sub1_data["depth_level"] == 2

    # 子账号 2: 信通院数据工程人员 (挂在子账号 1 下，形成 3 级物化树)
    sub2_req = {
        "username": "data_engineer_bob",
        "password": "Password123!@#",
        "display_name": "鲍勃(数据工程师)",
        "phone": "13900020002",
        "parent_user_id": sub1_uid,  # 挂在 sub1 下
        "workspace_ids": [default_ws],
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:caict_data:module:*"],
    }
    res_sub2 = client.post("/api/v1/uc/sub-account/create", json=sub2_req, headers=headers_master)
    assert res_sub2.status_code == 200, f"Create sub2 failed: {res_sub2.text}"
    sub2_data = res_sub2.json()["data"]
    sub2_uid = sub2_data["user_id"]
    print(f"  -> 子账号 2 (三级) 已创建: id={sub2_uid}, path={sub2_data['tree_path']}, depth={sub2_data['depth_level']}")
    assert sub2_data["tree_path"] == f"/{master_uid}/{sub1_uid}/{sub2_uid}/"
    assert sub2_data["depth_level"] == 3
    log_pass("多级企业子账号物化路径 (/master/sub1/sub2/) 与层级深度计算正确")

    # -------------------------------------------------------------
    # Step 6: 企业主子组织树列表查询
    # -------------------------------------------------------------
    log_step(6, "企业组织树结构查询 (GET /api/v1/uc/sub-account/tree)")
    res_tree = client.get("/api/v1/uc/sub-account/tree", headers=headers_master)
    assert res_tree.status_code == 200
    tree_items = res_tree.json()["data"]
    print(f"  -> 企业组织树节点清单 (共 {len(tree_items)} 个账号):")
    for item in tree_items:
        indent = "    " * item["depth_level"]
        print(f"{indent}|-- [{item['account_type']}] {item['username']} ({item['user_id']}) 深度:{item['depth_level']} 路径:{item['tree_path']}")
    assert len(tree_items) >= 3
    log_pass("组织树层级遍历结构完整，主子账号关联无断层")

    # -------------------------------------------------------------
    # Step 7: 工作空间多租户隔离与创建
    # -------------------------------------------------------------
    log_step(7, "新建项目工作空间并关联算力底座 (POST /api/v1/perm/workspace/create)")
    new_ws_req = {
        "workspace_name": "具身智能联合创新空间",
        "k8s_namespace": "ns-embodied-ai-01",
        "model_project_id": "prj-embodied-2026",
        "agent_space_id": "sbx-embodied-01",
        "quota_config_json": {"gpu": "16*H100", "memory": "512G", "storage": "2TB"},
    }
    res_ws_create = client.post("/api/v1/perm/workspace/create", json=new_ws_req, headers=headers_master)
    assert res_ws_create.status_code == 200
    new_ws_id = res_ws_create.json()["data"]["workspace_id"]
    print(f"  -> 新项目空间创建成功: id={new_ws_id}, name={new_ws_req['workspace_name']}")

    res_ws_list = client.get("/api/v1/perm/workspace/list", headers=headers_master)
    assert res_ws_list.status_code == 200
    ws_list = res_ws_list.json()["data"]
    print(f"  -> 当前企业工作空间总数: {len(ws_list)}")
    assert len(ws_list) >= 2
    log_pass("工作空间新建与底座 K8s Namespace / 模型项目绑定成功")

    # -------------------------------------------------------------
    # Step 8: 跨账号平台模块准入权限隔离
    # -------------------------------------------------------------
    log_step(8, "跨账号平台大模块准入权限隔离验证 (GET /api/v1/perm/user/modules-and-roles)")
    # Alice (模型专家) 访问 MODEL_ENGINE
    headers_alice = {"X-Portal-User-Id": sub1_uid, "X-Portal-Tenant-Id": tenant_id}
    res_alice = client.get(f"/api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id={default_ws}", headers=headers_alice)
    assert res_alice.status_code == 200
    alice_modules = res_alice.json()["data"]["allowed_modules"]
    print(f"  -> Alice 在 MODEL_ENGINE 准入模块: {alice_modules}")
    assert "platform:model_engine:module:*" in alice_modules

    # Bob (数据工程师) 访问 MODEL_ENGINE (无权，应被过滤隔离)
    headers_bob = {"X-Portal-User-Id": sub2_uid, "X-Portal-Tenant-Id": tenant_id}
    res_bob = client.get(f"/api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id={default_ws}", headers=headers_bob)
    assert res_bob.status_code == 200
    bob_modules = res_bob.json()["data"]["allowed_modules"]
    print(f"  -> Bob 在 MODEL_ENGINE 准入模块: {bob_modules}")
    assert "platform:model_engine:module:*" not in bob_modules
    assert len(bob_modules) == 0, "Bob 不应该拥有任何模型工程模块权限"
    log_pass("研发子账号跨平台模块白名单严格隔离，未越权泄露")

    # -------------------------------------------------------------
    # Step 9: 企业子账号门户登录兜底
    # -------------------------------------------------------------
    log_step(9, "企业子账号登录门户兜底验证 (POST /api/v1/sso/sub-login)")
    sub_login_req = {
        "username": "algo_expert_alice",
        "password": "Password123!@#",
    }
    res_sub_login = client.post("/api/v1/sso/sub-login", json=sub_login_req)
    assert res_sub_login.status_code == 200, f"Sub login failed: {res_sub_login.text}"
    sub_login_data = res_sub_login.json()["data"]
    print(f"  -> 子账号登录成功: user_id={sub_login_data['user_id']}, tenant={sub_login_data['tenant_name']}, roles={sub_login_data['roles']}")
    assert sub_login_data["user_id"] == sub1_uid
    assert sub_login_data["account_type"] == "SUB"
    assert "TENANT_DEVELOPER" in sub_login_data["roles"]
    assert sub_login_data["tenant_name"] == "昆山数字智能科技有限公司"
    log_pass("企业子账号成功通过 Casdoor 密码校验接入门户登录")

    # -------------------------------------------------------------
    # Step 10: 跨平台 60s 免密 Jump Ticket 签发与原子核销防重放
    # -------------------------------------------------------------
    log_step(10, "60s 免密 Jump Ticket 签发与原子核销防重放 (POST /api/v1/auth/jump-ticket/*)")
    issue_req = {
        "target_platform": "MODEL_ENGINE",
        "workspace_id": default_ws,
    }
    res_ticket = client.post("/api/v1/auth/jump-ticket/issue", json=issue_req, headers=headers_alice)
    assert res_ticket.status_code == 200
    ticket_cipher = res_ticket.json()["data"]["jump_ticket"]
    print(f"  -> 签发 Jump Ticket: {ticket_cipher[:32]}... (时效: 60秒)")

    # 第 1 次原子核销
    verify_req = {
        "jump_ticket": ticket_cipher,
        "caller_platform": "MODEL_ENGINE",
    }
    res_v1 = client.post("/api/v1/auth/jump-ticket/verify", json=verify_req)
    assert res_v1.status_code == 200, f"Verify failed: {res_v1.text}"
    v1_ctx = res_v1.json()["data"]
    print(f"  -> 第 1 次核销成功: uid={v1_ctx['user_id']}, master_id={v1_ctx['master_user_id']}, ws={v1_ctx['active_workspace_id']}, k8s_ns={v1_ctx['k8s_namespace']}")
    assert v1_ctx["user_id"] == sub1_uid
    assert v1_ctx["master_user_id"] == master_uid, "子账号必须正确追溯主账号"
    log_pass("第 1 次核销成功，完整上下文与物理底座标识准确呈现")

    # 第 2 次重复核销 (必须被拦截)
    res_v2 = client.post("/api/v1/auth/jump-ticket/verify", json=verify_req)
    print(f"  -> 第 2 次重复核销拦截响应: HTTP {res_v2.status_code} - {res_v2.text}")
    assert res_v2.status_code == 403
    log_pass("Redis GETDEL 原子保证单次有效，二次核销被严格拦截 (HTTP 403)")

    # -------------------------------------------------------------
    # Step 11: 网关 9 大 Header 染色与 HMAC-SHA256 签名防篡改
    # -------------------------------------------------------------
    log_step(11, "网关 9 大可信请求头染色与 HMAC-SHA256 签名算法防篡改校验")
    gw_headers = v1_ctx["gateway_headers"]
    print("  -> 生成的 9 大可信请求头样例:")
    for k, v in gw_headers.items():
        print(f"     * {k}: {v}")

    # 签名自校验
    assert verify_gateway_signature(gw_headers) is True
    log_pass("HMAC-SHA256 网关签名自验证通过")

    # 篡改 Header 防御测试
    tampered_headers = gw_headers.copy()
    tampered_headers["X-Portal-User-Id"] = "attacker_evil_uid"
    assert verify_gateway_signature(tampered_headers) is False
    log_pass("篡改请求头导致签名哈希不一致，被安全网关精准拦截")

    print("""
    ######################################################################
    #  [CONGRATULATIONS] 第一目标后端 11 大模块全链路断言 100% 全部通过!  #
    #  后端服务 (iam-service) 第一目标已具备正式封板交付条件!              #
    ######################################################################
    """)


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"\n[FATAL ERROR] 测试未通过: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
