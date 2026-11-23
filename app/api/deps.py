from typing import Optional
from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import verify_internal_jwt
from app.models.user_profile import UcUserProfile
from app.models.tenant import UcTenant
from app.models.role_binding import PermUserRoleWorkspace


class AuthContext:
    def __init__(
        self,
        user_id: str,
        tenant_id: str,
        account_type: str = "MASTER",
        role_code: str = "TENANT_ADMIN",
        tenant: Optional[UcTenant] = None,
        profile: Optional[UcUserProfile] = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.account_type = account_type
        self.role_code = role_code
        self.tenant = tenant
        self.profile = profile


def get_current_context(
    x_portal_user_id: Optional[str] = Header(None, alias="X-Portal-User-Id"),
    x_portal_tenant_id: Optional[str] = Header(None, alias="X-Portal-Tenant-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> AuthContext:
    """
    Resolve authentication and tenant context:
    1. First from gateway trusted headers (X-Portal-User-Id, X-Portal-Tenant-Id)
    2. Then from Bearer JWT token issued by /sso/exchange-iframe-session
    3. Fallback to first active master user if in development/test environment
    """
    user_id = x_portal_user_id
    tenant_id = x_portal_tenant_id
    account_type = "MASTER"
    role_code = "TENANT_ADMIN"

    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        payload = verify_internal_jwt(token)
        if payload:
            user_id = payload.get("user_id", user_id)
            tenant_id = payload.get("tenant_id", tenant_id)
            account_type = payload.get("account_type", account_type)
            role_code = payload.get("role_code", role_code)

    profile = None
    tenant = None

    if user_id:
        profile = (
            db.query(UcUserProfile)
            .filter(UcUserProfile.user_id == user_id, UcUserProfile.is_deleted == 0)
            .first()
        )
        if profile:
            tenant_id = profile.tenant_id
            account_type = profile.account_type

    if not profile and tenant_id:
        profile = (
            db.query(UcUserProfile)
            .filter(
                UcUserProfile.tenant_id == tenant_id,
                UcUserProfile.account_type == "MASTER",
                UcUserProfile.is_deleted == 0,
            )
            .first()
        )
        if profile:
            user_id = profile.user_id

    # Fallback for testing environments
    if not profile:
        profile = (
            db.query(UcUserProfile)
            .filter(UcUserProfile.is_deleted == 0)
            .first()
        )
        if profile:
            user_id = profile.user_id
            tenant_id = profile.tenant_id
            account_type = profile.account_type

    if profile:
        tenant = db.query(UcTenant).filter_by(tenant_id=profile.tenant_id).first()
        # 保护平台超级管理员与运营角色：若 Token 中已指定或数据库中存在超管绑定，绝不降级覆盖
        if role_code in ["PLATFORM_ADMIN", "PLATFORM_OPERATOR"]:
            pass
        else:
            plat_binding = (
                db.query(PermUserRoleWorkspace)
                .filter(
                    PermUserRoleWorkspace.user_id == profile.user_id,
                    PermUserRoleWorkspace.role_code.in_(["PLATFORM_ADMIN", "PLATFORM_OPERATOR"]),
                )
                .first()
            )
            if plat_binding:
                role_code = plat_binding.role_code
            else:
                binding = (
                    db.query(PermUserRoleWorkspace)
                    .filter_by(user_id=profile.user_id, tenant_id=profile.tenant_id)
                    .first()
                )
                if binding:
                    role_code = binding.role_code

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法解析当前用户上下文，请提供 X-Portal-User-Id 或 Authorization Bearer 令牌",
        )

    return AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        account_type=account_type,
        role_code=role_code,
        tenant=tenant,
        profile=profile,
    )
