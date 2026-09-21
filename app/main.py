"""实验室样品分装谱系 API。

- 登记带体积的初始样品
- 批量提交依次执行的容器间转移（原子：任一失败整批回滚）
- 查询任一容器当前体积与祖先来源占比
"""
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, services
from .database import engine, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 容器启动时建库建表
    models.Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Sample Aliquot Lineage API",
    version="1.0.0",
    description="实验室样品分装谱系追踪：登记初始样品、批量转移、查询来源占比。",
    lifespan=lifespan,
)


def _container_state(db: Session, container: models.Container) -> schemas.ContainerState:
    rows = db.scalars(
        select(models.Composition)
        .where(models.Composition.container_id == container.id)
        .order_by(models.Composition.sample_id)
    ).all()
    return schemas.ContainerState(
        container_id=container.id,
        volume=container.volume,
        composition=[
            schemas.CompositionEntry(
                sample_id=row.sample_id,
                fraction=row.fraction,
                percentage=str(Decimal(row.fraction) * 100),
            )
            for row in rows
        ],
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(
    "/samples",
    response_model=schemas.ContainerState,
    status_code=status.HTTP_201_CREATED,
)
def register_sample(payload: schemas.SampleRegister, db: Session = Depends(get_db)):
    """登记初始样品：创建容器，来源 100% 为该样品。"""
    try:
        container = services.register_sample(
            db, payload.container_id, payload.volume, payload.sample_id
        )
    except services.BatchRejection as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return _container_state(db, container)


@app.post("/transfers", response_model=schemas.BatchAccepted)
def submit_transfers(batch: schemas.TransferBatch, db: Session = Depends(get_db)):
    """批量提交转移，按顺序依次执行；任一失败则整批拒绝、状态不变。"""
    results: list[schemas.TransferResult] = []
    try:
        for index, transfer in enumerate(batch.transfers):
            outcome = services.apply_transfer(
                db,
                index,
                transfer.source,
                transfer.destination,
                transfer.volume,
            )
            results.append(
                schemas.TransferResult(
                    index=index,
                    source=transfer.source,
                    destination=transfer.destination,
                    volume=str(transfer.volume),
                    **outcome,
                )
            )
        db.commit()
    except services.BatchRejection as exc:
        db.rollback()  # 整批回滚，状态不变
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "batch_rejected": True,
                "failed_index": exc.index,
                "reason": exc.reason,
                "message": exc.message,
            },
        ) from exc
    return schemas.BatchAccepted(applied=len(results), transfers=results)


@app.get("/containers", response_model=list[schemas.ContainerState])
def list_containers(db: Session = Depends(get_db)):
    containers = db.scalars(select(models.Container).order_by(models.Container.id)).all()
    return [_container_state(db, container) for container in containers]


@app.get("/containers/{container_id}", response_model=schemas.ContainerState)
def get_container(container_id: str, db: Session = Depends(get_db)):
    """查询容器当前体积及各祖先来源占比。"""
    container = db.get(models.Container, container_id)
    if container is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"未知容器 '{container_id}'"
        )
    return _container_state(db, container)
