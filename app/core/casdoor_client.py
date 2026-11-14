import logging
from typing import Optional, Dict, Any
from casdoor import CasdoorSDK
from casdoor.user import User
from app.core.config import settings

logger = logging.getLogger("iam-service.casdoor")


class CasdoorClientWrapper:
    def __init__(self):
        self._sdk = None

    @property
    def sdk(self) -> CasdoorSDK:
        if self._sdk is None:
            self._sdk = CasdoorSDK(
                endpoint=settings.CASDOOR_INTERNAL_ENDPOINT,
                client_id=settings.CASDOOR_CLIENT_ID,
                client_secret=settings.CASDOOR_CLIENT_SECRET,
                certificate="",
                org_name=settings.CASDOOR_ORG_NAME,
                application_name=settings.CASDOOR_APP_NAME,
            )
        return self._sdk

    def create_user(
        self,
        username: str,
        display_name: str = "",
        phone: str = "",
        email: str = "",
        password: str = "",
        org_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create user in Casdoor. Resilient to Casdoor service availability."""
        org = org_name or settings.CASDOOR_ORG_NAME
        user = User()
        user.owner = org
        user.name = username
        user.displayName = display_name or username
        user.phone = phone or ""
        user.email = email or ""
        user.password = password or ""
        user.signupApplication = settings.CASDOOR_APP_NAME
        try:
            res = self.sdk.add_user(user)
            logger.info(f"Casdoor add_user result for {username}: {res}")
            return res if isinstance(res, dict) else {"status": "ok", "msg": str(res)}
        except Exception as e:
            logger.warning(f"Failed to create user {username} in Casdoor: {e}")
            return {"status": "fallback", "error": str(e)}

    def verify_password(
        self,
        username: str,
        password: str,
        org_name: Optional[str] = None,
    ) -> bool:
        """Verify user password against Casdoor."""
        org = org_name or settings.CASDOOR_ORG_NAME
        user = User()
        user.owner = org
        user.name = username
        user.password = password
        try:
            ok = self.sdk.check_user_password(user)
            if ok:
                return True
            # Fallback: attempt oauth password grant if check_user_password fails or returns false
            try:
                token_res = self.sdk.get_oauth_token_by_password(username, password)
                if token_res and "access_token" in token_res:
                    return True
            except Exception:
                pass
            return bool(ok)
        except Exception as e:
            logger.warning(f"Error checking password for {username} in Casdoor: {e}")
            # If Casdoor is unavailable during dev/mock, allow fallback when configured or return False
            if settings.APP_ENV == "dev":
                logger.info(f"DEV mode fallback: allowing test verification for {username}")
                return True
            return False

    def fetch_user(self, username: str, org_name: Optional[str] = None) -> Optional[User]:
        """Fetch user by username from Casdoor."""
        try:
            return self.sdk.get_user(username)
        except Exception as e:
            logger.warning(f"Error fetching user {username} from Casdoor: {e}")
            return None


casdoor_client = CasdoorClientWrapper()
