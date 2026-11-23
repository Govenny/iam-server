"""
Test Admin Ops UI and API integration.
"""
import sys
import os
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_html_ui_admin_elements():
    """Verify web_dist/index.html contains all necessary admin elements and styles."""
    with open('web_dist/index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    required_admin_ids = [
        'view-switch-box', 'admin-toolbar', 'tenant-toolbar',
        'tab-admin-dashboard', 'tab-admin-tenants', 'tab-admin-users', 'tab-admin-audit', 'tab-admin-sync',
        'stat-tenants', 'stat-users', 'stat-workspaces', 'stat-jumps',
        'bar-model', 'bar-compute', 'bar-agent', 'bar-caict',
        'health-db', 'health-redis', 'health-casdoor', 'health-gw',
        'admin-tenant-kw', 'admin-tenant-tbody', 'modal-admin-tenant',
        'admin-user-kw', 'admin-user-tbody', 'modal-admin-user',
        'admin-audit-tbody', 'sync-info-file', 'sync-attr-file'
    ]

    for elem_id in required_admin_ids:
        assert f'id="{elem_id}"' in html, f"Missing element id: {elem_id}"

    # Verify color codes present in CSS
    assert '#1A4498' in html or '#1a4498' in html  # Primary
    assert '#0056AF' in html or '#0056af' in html  # Brand secondary
    assert '#FB704B' in html or '#fb704b' in html  # Coral accent
    assert '#2FD29B' in html or '#2fd29b' in html  # Green success

    print("[PASS] test_html_ui_admin_elements verified successfully.")

def test_ui_route_returns_page():
    """Verify /ui endpoint serves index.html."""
    resp = client.get("/ui")
    assert resp.status_code == 200
    assert "平台运营中枢" in resp.text
    assert "全域用户中心" in resp.text
    print("[PASS] test_ui_route_returns_page verified successfully.")

if __name__ == '__main__':
    test_html_ui_admin_elements()
    test_ui_route_returns_page()
    print("All Admin UI integration checks passed!")
