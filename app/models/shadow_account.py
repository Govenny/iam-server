from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UcShadowAccountMapping(Base):
    __tablename__ = "uc_shadow_account_mapping"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    mapping_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="映射唯一编码")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="门户全局租户ID")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="门户全局用户ID")
    target_platform_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="目标异构平台编码")
    shadow_tenant_id: Mapped[str] = mapped_column(String(128), default="", nullable=False, comment="异构系统内部租户ID")
    shadow_user_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="异构系统内部账号ID")
    shadow_account_name: Mapped[str] = mapped_column(String(128), default="", nullable=False, comment="异构系统内部登录名")
    mapping_type: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="1-JIT静默创号, 2-手工绑定")
    sync_status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="1-正常, 2-同步中, 3-失败")
    ext_meta_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=True, comment="扩展凭据元数据")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
