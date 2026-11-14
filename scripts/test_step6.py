import os
import sys

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_ui_endpoints():
    print("==================================================")
    print(">>> 开始执行 Step 6: 前端嵌入页面与 /ui/* 路由测试")
    print("==================================================")

    # 1. 测试根 /ui
    print("\n[用例 1] 访问 /ui 根入口...")
    res = client.get("/ui")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "数智底座用户与角色权限中心" in res.text
    print("  [PASS] /ui 根路由访问正常，返回嵌入前端 HTML！")

    # 2. 测试 /ui/members 嵌入页面 (SPA 路由回退)
    print("\n[用例 2] 访问 /ui/members 成员管理页面...")
    res_m = client.get("/ui/members")
    assert res_m.status_code == 200
    assert "企业成员与角色树" in res_m.text
    assert "新建子账号" in res_m.text
    print("  [PASS] /ui/members 页面正常挂载并渲染！")

    # 3. 测试 /ui/workspaces 嵌入页面
    print("\n[用例 3] 访问 /ui/workspaces 工作空间管理页面...")
    res_w = client.get("/ui/workspaces")
    assert res_w.status_code == 200
    assert "工作空间与底层底座映射" in res_w.text
    print("  [PASS] /ui/workspaces 页面正常挂载并渲染！")

    # 4. 测试 /ui/apikeys 嵌入页面
    print("\n[用例 4] 访问 /ui/apikeys API Key 页面...")
    res_a = client.get("/ui/apikeys")
    assert res_a.status_code == 200
    assert "能力商店 API Key 凭证" in res_a.text
    print("  [PASS] /ui/apikeys 页面正常挂载并渲染！")

    # 5. 测试携带门户 Token 访问 (/ui/members?portal_token=token_p_1001)
    print("\n[用例 5] 携带门户 Token 访问 /ui/members?portal_token=xxx...")
    res_tok = client.get("/ui/members?portal_token=token_p_1001_zhangsan")
    assert res_tok.status_code == 200
    assert "initSession" in res_tok.text
    print("  [PASS] 门户 iframe 携带 Token 访问流程正常！")

    print("\n==================================================")
    print(">>> Step 6 全部 5 项测试断言 100% 通过 [ALL PASS]！")
    print("==================================================")


if __name__ == "__main__":
    test_ui_endpoints()
