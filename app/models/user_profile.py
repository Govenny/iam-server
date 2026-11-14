from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UcUserProfile(Base):
    __tablename__ = "uc_user_profile"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="全局用户ID")
    portal_user_id: Mapped[str] = mapped_column(String(128), nullable=True, comment="存量门户系统用户ID")
    casdoor_user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="Casdoor原生 user.id")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属租户ID")
    account_type: Mapped[str] = mapped_column(String(32), default="MASTER", nullable=False, comment="MASTER/SUB")
    parent_user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="直接上级用户ID")
    tree_path: Mapped[str] = mapped_column(String(512), nullable=False, comment="物化路径")
    depth_level: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="层级深度")
    quota_share_enabled: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="是否共享配额")
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="1-正常, 2-冻结, 3-注销")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
