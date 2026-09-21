"""谱系核心逻辑：按源容器当前混合比例传播祖先来源，全程 Decimal 运算。"""
from decimal import Decimal, getcontext

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Composition, Container

# 提高 Decimal 上下文精度，保证长链转移后占比依然精确
getcontext().prec = 50

ONE = Decimal(1)
ZERO = Decimal(0)


class BatchRejection(Exception):
    """批处理中某条转移违反业务规则，整批拒绝。"""

    def __init__(self, index: int, reason: str, message: str):
        super().__init__(message)
        self.index = index
        self.reason = reason
        self.message = message


def _fractions(db: Session, container_id: str) -> dict[str, Decimal]:
    rows = db.scalars(
        select(Composition).where(Composition.container_id == container_id)
    ).all()
    return {row.sample_id: Decimal(row.fraction) for row in rows}


def _normalize(amounts: dict[str, Decimal], total: Decimal) -> dict[str, Decimal]:
    """把各来源的绝对量换算成占比，并修正使占比之和精确等于 1。"""
    fracs = {sid: amount / total for sid, amount in amounts.items() if amount > 0}
    if not fracs:
        return fracs
    residual = ONE - sum(fracs.values())
    if residual != 0:
        # 把舍入残差并入占比最大的来源，保证 sum(fraction) == 1 精确成立
        key = max(fracs, key=lambda sid: fracs[sid])
        fracs[key] += residual
    return fracs


def _replace_composition(
    db: Session, container_id: str, fracs: dict[str, Decimal]
) -> None:
    db.execute(delete(Composition).where(Composition.container_id == container_id))
    db.add_all(
        Composition(container_id=container_id, sample_id=sid, fraction=str(frac))
        for sid, frac in sorted(fracs.items())
    )


def register_sample(
    db: Session, container_id: str, volume: Decimal, sample_id: str | None
) -> Container:
    if db.get(Container, container_id) is not None:
        raise BatchRejection(-1, "container_exists", f"容器 '{container_id}' 已存在")
    container = Container(id=container_id, volume=str(volume))
    db.add(container)
    db.add(
        Composition(
            container_id=container_id,
            sample_id=sample_id or container_id,
            fraction=str(ONE),
        )
    )
    db.commit()
    db.refresh(container)
    return container


def apply_transfer(
    db: Session, index: int, source_id: str, destination_id: str, volume: Decimal
) -> dict[str, str]:
    """执行一次转移；违反规则时抛出 BatchRejection（由调用方回滚整批）。"""
    if source_id == destination_id:
        raise BatchRejection(index, "self_transfer", f"容器 '{source_id}' 不能向自身转移")

    source = db.get(Container, source_id)
    if source is None:
        raise BatchRejection(index, "unknown_container", f"未知容器 '{source_id}'")
    destination = db.get(Container, destination_id)
    if destination is None:
        raise BatchRejection(
            index, "unknown_container", f"未知容器 '{destination_id}'"
        )

    source_volume = Decimal(source.volume)
    if volume > source_volume:
        raise BatchRejection(
            index,
            "insufficient_volume",
            f"容器 '{source_id}' 余量 {source.volume}，不足以转移 {volume}",
        )

    destination_volume = Decimal(destination.volume)
    new_source_volume = source_volume - volume
    new_destination_volume = destination_volume + volume

    # 按源容器当前混合比例，把全部祖先来源等比带入目标容器
    source_fracs = _fractions(db, source_id)
    destination_fracs = _fractions(db, destination_id)

    # 先算目标容器中各来源的绝对量，再归一化为占比
    amounts = {
        sid: frac * destination_volume for sid, frac in destination_fracs.items()
    }
    for sid, frac in source_fracs.items():
        amounts[sid] = amounts.get(sid, ZERO) + frac * volume

    source.volume = str(new_source_volume)
    destination.volume = str(new_destination_volume)
    # 源容器混合均匀，移出后各来源占比不变，无需改写
    _replace_composition(db, destination_id, _normalize(amounts, new_destination_volume))
    # 立即落盘（事务内），保证同批后续转移读到最新状态
    db.flush()

    return {
        "source_volume_after": str(new_source_volume),
        "destination_volume_after": str(new_destination_volume),
    }
