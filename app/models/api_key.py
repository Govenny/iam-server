from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, SmallInteger, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PermApiKey(Base):
    __tablename__ = "perm_api_key"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="内部唯一标识")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属租户ID")
    created_by_uid: Mapped[str] = mapped_column(String(64), nullable=False, comment="创建人user_id")
    key_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="凭证助记名称")
    access_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="外部调用Key")
    secret_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="密钥哈希")
    salt: Mapped[str] = mapped_column(String(32), nullable=False, comment="加盐串")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False, comment="ACTIVE/ROTATING/DISABLED/REVOKED")
    allowed_scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="服务URI白名单")
    ip_whitelist: Mapped[str] = mapped_column(String(512), default="", nullable=False, comment="IP白名单CIDR")
    qps_limit: Mapped[int] = mapped_column(Integer, default=100, nullable=False, comment="每秒限流")
    daily_call_limit: Mapped[int] = mapped_column(BigInteger, default=100000, nullable=False, comment="日调用上限")
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, comment="失效时间")
    grace_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=True, comment="双Key过渡截止时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
