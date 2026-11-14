from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PermTenantWorkspace(Base):
    __tablename__ = "perm_tenant_workspace"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="工作空间全局唯一标识")
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属租户ID")
    workspace_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工作空间显示名称")
    is_default: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="是否默认主空间")
    k8s_namespace: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="映射K8s Namespace")
    model_project_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="映射模型平台项目ID")
    agent_space_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="映射Agent平台沙箱ID")
    caict_workspace_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="映射信通院空间ID")
    quota_config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False, comment="空间配额JSON")
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="1-活跃, 2-冻结, 3-释放")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
