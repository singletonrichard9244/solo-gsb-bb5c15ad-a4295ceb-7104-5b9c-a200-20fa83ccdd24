"""数据库引擎与会话管理。

通过环境变量 DATABASE_URL 配置，默认使用项目目录下 data/lineage.db。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/lineage.db")


def _build_engine(url: str):
    if url.startswith("sqlite"):
        # 确保 SQLite 文件所在目录存在（内存库除外）
        if ":memory:" not in url:
            db_path = url.split("sqlite:///", 1)[-1]
            directory = os.path.dirname(db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            # 内存库需要共享同一连接，否则各连接看到不同的库
            return create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
            )
        return create_engine(url, connect_args=connect_args)
    return create_engine(url)


engine = _build_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
