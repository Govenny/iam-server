from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PermUserRoleWorkspace(Base):
    __tablename__ = "perm_user_role_workspace"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属租户ID")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="被授权用户ID")
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="绑定的工作空间ID")
    role_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="7大标准角色编码")
    allowed_modules: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="平台模块准入数组")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
