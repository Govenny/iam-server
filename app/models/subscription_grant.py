from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PermServiceSubscriptionGrant(Base):
    __tablename__ = "perm_service_subscription_grant"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    grant_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="授权流水号")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="被授权租户ID")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="来源类型")
    source_contract_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="关联订单/契约编号")
    resource_uri: Mapped[str] = mapped_column(String(255), nullable=False, comment="被授权资产URI")
    granted_actions: Mapped[str] = mapped_column(String(64), default="READ", nullable=False, comment="授权动作集合")
    grant_status: Mapped[str] = mapped_column(String(32), default="EFFECTIVE", nullable=False, comment="状态")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, comment="生效起始")
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, comment="授权终止")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
