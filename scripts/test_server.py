#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数智底座用户与角色权限中心 — 服务器端一键全链路测试脚本
使用 Python 标准库 (无需额外安装 pip 包，服务器直接运行)

用法:
  python scripts/test_server.py
  或者指定服务器地址:
  python scripts/test_server.py http://10.212.131.6:8001
"""

import sys
import json
import urllib.request
import urllib.error
import urllib.parse
import time

DEFAULT_BASE_URL = "http://127.0.0.1:8001"
BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE_URL


def http_request(method, path, body=None, headers=None):
    url = f"{BASE_URL}{path}"
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            res_body = resp.read().decode("utf-8")
            return code, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"raw": err_body}
    except Exception as e:
        return 0, {"error": str(e)}


def log_step(step, name):
    print(f"\n[{step}/8] >>> {name}...")


def main():
    print("=" * 65)
    print(f"🚀 开始测试 iam-service 伴生服务")
    print(f"🌐 目标测试地址: {BASE_URL}")
    print("=" * 65)

    # 1. 健康探活
    log_step(1, "服务健康探活 (GET /health)")
    code, res = http_request("GET", "/health")
    if code != 200 or res.get("status") != "healthy":
        print(f"❌ 服务无法连接或未就绪! HTTP {code}, 响应: {res}")
        print("请检查容器是否运行: docker ps | grep iam-service")
        sys.exit(1)
    print(f"   ✅ 服务正常运行中: {res}")

    # 2. 门户同步个人用户
    log_step(2, "模拟门户注册/登录静默双写同步 (POST /api/v1/sync/user)")
    ts = int(time.time())
    portal_uid = f"p_{ts}"
    sync_req = {
        "portal_user_id": portal_uid,
        "username": f"user_{portal_uid}",
        "display_name": f"测试企业主_{portal_uid[-4:]}",
        "phone": f"138{ts % 100000000:08d}",
        "is_platform_admin": False,
        "is_enterprise": False,
        "company_name": "",
    }
    code, res = http_request("POST", "/api/v1/sync/user", sync_req)
    if code != 200:
        print(f"❌ 用户同步失败: {res}")
        sys.exit(1)
    data = res["data"]
    master_uid = data["user_id"]
    tenant_id = data["tenant_id"]
    default_ws = data["default_workspace_id"]
    print(f"   ✅ 同步成功: 用户ID={master_uid}, 租户={tenant_id}, 类型={data['tenant_type']}, 空间={default_ws}")

    # 3. 门户审核企业认证通过
    log_step(3, "模拟门户后台审核通过企业认证 (POST /api/v1/sync/enterprise-approved)")
    approve_req = {
        "portal_user_id": portal_uid,
        "company_name": "昆山测试人工智能产业研究院",
        "credit_code": "91320583MA1TEST888",
        "legal_person_name": "张总",
    }
    code, res = http_request("POST", "/api/v1/sync/enterprise-approved", approve_req)
    if code != 200:
        print(f"❌ 企业升级失败: {res}")
        sys.exit(1)
    print(f"   ✅ 租户成功升级为企业租户: {res['data']['tenant_name']} (cert_level=2)")

    # 4. 创建企业子账号
    log_step(4, "企业主账号创建研发子账号 (POST /api/v1/uc/sub-account/create)")
    headers_master = {
        "X-Portal-User-Id": master_uid,
        "X-Portal-Tenant-Id": tenant_id,
    }
    sub_uname = f"algo_dev_{portal_uid[-4:]}"
    sub_req = {
        "username": sub_uname,
        "password": "Password123!@#",
        "display_name": "李工(模型专家)",
        "phone": f"139{ts % 100000000:08d}",
        "parent_user_id": master_uid,
        "workspace_ids": [default_ws],
        "role_code": "TENANT_DEVELOPER",
        "allowed_modules": ["platform:model_engine:module:*"],
    }
    code, res = http_request("POST", "/api/v1/uc/sub-account/create", sub_req, headers=headers_master)
    if code != 200:
        print(f"❌ 创建子账号失败: {res}")
        sys.exit(1)
    sub_uid = res["data"]["user_id"]
    print(f"   ✅ 子账号创建成功: 用户名={sub_uname}, 用户ID={sub_uid}, 物化路径={res['data']['tree_path']}")

    # 5. 查询企业主子树
    log_step(5, "查询企业组织树列表 (GET /api/v1/uc/sub-account/tree)")
    code, res = http_request("GET", "/api/v1/uc/sub-account/tree", headers=headers_master)
    if code != 200:
        print(f"❌ 查询组织树失败: {res}")
        sys.exit(1)
    tree_items = res["data"]
    print(f"   ✅ 组织树查询成功 (共 {len(tree_items)} 个成员):")
    for item in tree_items:
        print(f"      - [{item['account_type']}] {item['username']} ({item['user_id']}) 深度={item['depth_level']}, 角色={item['roles']}")

    # 6. 子账号登录门户兜底验证
    log_step(6, "企业子账号登录门户兜底校验 (POST /api/v1/sso/sub-login)")
    sub_login_req = {
        "username": sub_uname,
        "password": "Password123!@#",
    }
    code, res = http_request("POST", "/api/v1/sso/sub-login", sub_login_req)
    if code != 200:
        print(f"❌ 子账号登录失败: {res}")
        sys.exit(1)
    print(f"   ✅ 子账号登录验证成功: 所属企业={res['data']['tenant_name']}, 角色={res['data']['roles']}")

    # 7. 60秒 Jump Ticket 签发与原子核销
    log_step(7, "跨平台 60s 免密 Jump Ticket 签发与单次核销防重放")
    headers_sub = {"X-Portal-User-Id": sub_uid, "X-Portal-Tenant-Id": tenant_id}
    issue_req = {"target_platform": "MODEL_ENGINE", "workspace_id": default_ws}
    code, res = http_request("POST", "/api/v1/auth/jump-ticket/issue", issue_req, headers=headers_sub)
    if code != 200:
        print(f"❌ 签发票据失败: {res}")
        sys.exit(1)
    ticket = res["data"]["jump_ticket"]
    print(f"   🎟️  票据签发成功: {ticket[:24]}... (60s 有效)")

    # 第一次核销 (预期 200)
    verify_req = {"jump_ticket": ticket, "caller_platform": "MODEL_ENGINE"}
    code, res = http_request("POST", "/api/v1/auth/jump-ticket/verify", verify_req)
    if code != 200:
        print(f"❌ 第一次核销失败: {res}")
        sys.exit(1)
    print(f"   ✅ 第 1 次原子核销成功: 用户={res['data']['user_id']}, 主账号溯源={res['data']['master_user_id']}, 空间={res['data']['active_workspace_id']}")

    # 第二次核销 (预期 403 拦截)
    code, res = http_request("POST", "/api/v1/auth/jump-ticket/verify", verify_req)
    if code == 403:
        print(f"   🛡️  第 2 次核销防重放拦截成功 (HTTP 403): {res.get('SYS_HEAD', {}).get('RESP_DESC') or res.get('detail', '票据已核销')}")
    else:
        print(f"❌ 防重放拦截失败: 预期 403，实际返回 {code}")
        sys.exit(1)

    # 8. 国家中试基地统一接口规范 2.4.3 queryUserInfo 验证
    log_step(8, "国家中试基地数智中台统一接口 (POST /api/customer/cuseser/v1)")
    query_req = {
        "SYS_HEAD": {
            "APP_ID": "APP_SZ_PILOT_001",
            "TIMESTAMP": time.strftime("%Y-%m-%d %H:%M:%S 000"),
            "TRANS_ID": f"{time.strftime('%Y%m%d%H%M%S')}000123456",
            "RESERVED": [],
        },
        "SYS_BODY": {
            "_QUERY_USER_INFO_REQ": {
                "USER_ID": master_uid,
                "QUERY_TYPE": "DETAIL",
            }
        },
    }
    code, res = http_request("POST", "/api/customer/cuseser/v1", query_req)
    if code == 200 and res.get("SYS_HEAD", {}).get("RESP_CODE") == "00000":
        user_rsp = res.get("SYS_BODY", {}).get("_QUERY_USER_INFO_RSP", {})
        print(f"   ✅ 国家中试基地标准查询成功: 用户={user_rsp.get('USER_NAME')}, 归属={user_rsp.get('PROVINCE_CODE')}/{user_rsp.get('EPARCHY_CODE')}, 状态={user_rsp.get('STATUS')}")
        print(f"      SYS_HEAD.RESP_CODE={res['SYS_HEAD']['RESP_CODE']}, TRANS_ID={res['SYS_HEAD']['TRANS_ID']}")
    else:
        print(f"❌ 中台统一接口查询失败: HTTP {code}, 响应: {res}")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("🎉 恭喜！服务器全链路核心业务与中台统一接口测试 100% 全部通过 [ALL PASS]！")
    print("=" * 65)
    print("\n💡 接下来您可以验证 Casdoor 8000 后台：")
    print("   1. 打开浏览器访问: http://<服务器IP>:8000/users")
    print("   2. 使用 admin / 123 登录 Casdoor 后台")
    print(f"   3. 在用户列表中，您将看到刚才自动同步的主账号 '{sync_req['username']}' 和子账号 '{sub_uname}'！")
    print(f"\n💡 您也可以在浏览器直接体验嵌入前端页面：")
    print(f"   http://<服务器IP>:8001/ui/members?portal_token=token_{portal_uid}\n")


if __name__ == "__main__":
    main()
