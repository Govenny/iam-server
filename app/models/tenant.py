from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UcTenant(Base):
    __tablename__ = "uc_tenant"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="全局租户唯一标识")
    tenant_type: Mapped[str] = mapped_column(String(32), default="PERSONAL", nullable=False, comment="租户类型: PERSONAL/ENTERPRISE")
    org_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="Casdoor原生 organization.name")
    tenant_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="租户英文代号")
    tenant_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="企业全称或个人租户名称")
    cert_level: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="实名等级: 0-未实名, 1-个人实名, 2-企业实名")
    credit_code: Mapped[str] = mapped_column(String(18), nullable=True, comment="统一社会信用代码")
    legal_person_name: Mapped[str] = mapped_column(String(64), nullable=True, comment="法人/个人姓名")
    cert_status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="实名状态: 0-未认证, 1-审核中, 2-已通过, 3-已驳回, 4-已过期, 5-已吊销")
    trusted_data_synced: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="可信流通同步标记: 0-未同步, 1-已同步")
    is_exempt_billing: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="免计费白名单: 0-正常计费, 1-免扣费")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
