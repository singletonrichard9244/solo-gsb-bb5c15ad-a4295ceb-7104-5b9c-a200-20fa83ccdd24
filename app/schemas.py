"""Pydantic 请求/响应模型。

体积使用 Decimal；建议客户端以 JSON 字符串传参（如 "10.5"），
避免 JSON 浮点数在传输中损失精度。
"""
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SampleRegister(BaseModel):
    """登记初始样品：放入一个容器，初始来源即样品自身。"""

    container_id: str = Field(min_length=1, max_length=128, examples=["tube-A"])
    volume: Decimal = Field(gt=0, examples=["10.0"])
    # 样品（来源）名称，缺省与容器同名
    sample_id: str | None = Field(default=None, min_length=1, max_length=128)


class Transfer(BaseModel):
    source: str = Field(min_length=1, max_length=128)
    destination: str = Field(min_length=1, max_length=128)
    volume: Decimal = Field(gt=0)


class TransferBatch(BaseModel):
    """一批依次执行的转移；任一失败则整批回滚。"""

    transfers: list[Transfer] = Field(min_length=1)


class CompositionEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sample_id: str
    fraction: str  # Decimal 字符串，0~1
    percentage: str  # fraction * 100


class ContainerState(BaseModel):
    container_id: str
    volume: str  # Decimal 字符串
    composition: list[CompositionEntry]


class TransferResult(BaseModel):
    index: int
    source: str
    destination: str
    volume: str
    source_volume_after: str
    destination_volume_after: str


class BatchAccepted(BaseModel):
    applied: int
    transfers: list[TransferResult]
