"""SQLAlchemy 数据模型。

Decimal 值以字符串形式存储，避免 SQLite REAL 带来的浮点精度损失。
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Container(Base):
    """一个容器（试管、孔位等），持有当前体积。"""

    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 当前体积，Decimal 的字符串表示
    volume: Mapped[str] = mapped_column(String(128), nullable=False)

    composition: Mapped[list["Composition"]] = relationship(
        back_populates="container",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Composition(Base):
    """容器中某一原始样品（祖先来源）所占比例，fraction ∈ (0, 1]。"""

    __tablename__ = "compositions"

    container_id: Mapped[str] = mapped_column(
        ForeignKey("containers.id"), primary_key=True
    )
    sample_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 占比，Decimal 的字符串表示
    fraction: Mapped[str] = mapped_column(String(128), nullable=False)

    container: Mapped[Container] = relationship(back_populates="composition")
