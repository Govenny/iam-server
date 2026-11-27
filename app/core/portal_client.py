import hashlib
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger("iam-service.portal_client")


class PortalClient:
    """
    Client for the External Operation Portal Unified Auth System
    (matching 《门户-身份体系接口文档》: /api/sso/login, /api/sso/validateTicket,
    /api/sso/validateToken, /api/user/listUserInfoList, /api/company/listAuthCompanyList, /api/sso/logout).
    """

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return (settings.PORTAL_SSO_BASE_URL or "").rstrip("/")

    @property
    def client_id(self) -> str:
        return settings.PORTAL_SSO_CLIENT_ID or "test-client"

    @property
    def salt(self) -> str:
        return settings.PORTAL_AUTH_SALT or "4c4a1c7d52"

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def build_auth_sign(self, client_id: Optional[str] = None) -> str:
        """Compute authentication = MD5(clientId + 4c4a1c7d52)."""
        cid = client_id if client_id is not None else self.client_id
        raw = f"{cid}{self.salt}".encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    @staticmethod
    def _unwrap_data(payload: Any) -> Any:
        """Extract nested 'data' if portal wraps response in {code, message, data}."""
        if isinstance(payload, dict) and "data" in payload and payload["data"] is not None:
            return payload["data"]
        return payload

    def sso_login(self, session_id: str, client_id: Optional[str] = None) -> Dict[str, Any]:
        """Step 1: GET /api/sso/login?clientId=...&sessionId=... -> {ticket, callbackUrl}."""
        url = f"{self.base_url}/api/sso/login"
        params = {
            "clientId": client_id or self.client_id,
            "sessionId": session_id or "",
        }
        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(url, params=params)
            res.raise_for_status()
            data = self._unwrap_data(res.json())
            return data if isinstance(data, dict) else {}

    def validate_ticket(self, ticket: str, client_id: Optional[str] = None) -> Dict[str, Any]:
        """Step 2: GET /api/sso/validateTicket?clientId=...&ticket=... -> user info + token."""
        url = f"{self.base_url}/api/sso/validateTicket"
        params = {
            "clientId": client_id or self.client_id,
            "ticket": ticket,
        }
        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(url, params=params)
            res.raise_for_status()
            data = self._unwrap_data(res.json())
            return data if isinstance(data, dict) else {}

    def exchange_session_for_user(self, session_id: str, client_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute full 2-step Portal SSO exchange:
        1. sessionId -> 20s ticket
        2. ticket -> real user info
        Returns dict with {"authenticated": bool, "login_url": str, "user": dict}.
        """
        login_res = self.sso_login(session_id, client_id=client_id)
        ticket = login_res.get("ticket")
        callback_url = login_res.get("callbackUrl") or settings.PORTAL_LOGIN_URL
        if not ticket:
            return {
                "authenticated": False,
                "login_url": callback_url,
                "user": None,
            }

        user_info = self.validate_ticket(ticket, client_id=client_id)
        return {
            "authenticated": True,
            "login_url": callback_url,
            "user": user_info,
        }

    def validate_token(self, token: str) -> bool:
        """POST /api/sso/validateToken -> verify if portal token is still valid."""
        if not self.is_configured():
            return False
        url = f"{self.base_url}/api/sso/validateToken"
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(url, json={"token": token})
            res.raise_for_status()
            payload = res.json()
            if isinstance(payload, dict) and "data" in payload:
                return bool(payload["data"])
            return bool(payload)

    def sso_logout(self, token: str) -> bool:
        """POST /api/sso/logout -> notify portal to revoke user session."""
        if not self.is_configured():
            return False
        url = f"{self.base_url}/api/sso/logout"
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(url, json={"token": token})
            res.raise_for_status()
            return True

    def list_user_info(self, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /api/user/listUserInfoList with clientId + MD5 authentication."""
        cid = client_id if client_id is not None else self.client_id
        url = f"{self.base_url}/api/user/listUserInfoList"
        params = {
            "clientId": cid,
            "authentication": self.build_auth_sign(cid),
        }
        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(url, params=params)
            res.raise_for_status()
            data = self._unwrap_data(res.json())
            return data if isinstance(data, list) else []

    def list_auth_companies(self, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /api/company/listAuthCompanyList with clientId + MD5 authentication."""
        cid = client_id if client_id is not None else self.client_id
        url = f"{self.base_url}/api/company/listAuthCompanyList"
        params = {
            "clientId": cid,
            "authentication": self.build_auth_sign(cid),
        }
        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(url, params=params)
            res.raise_for_status()
            data = self._unwrap_data(res.json())
            return data if isinstance(data, list) else []


portal_client = PortalClient()
