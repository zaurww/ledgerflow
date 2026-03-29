"""
core/database.py — Подключение к базе данных

Один файл, одно подключение. Все модели импортируют Base отсюда.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://ledger:ledger_dev_password@localhost/ledgerflow"
    environment: str = "development"
    secret_key: str = "change_this_in_production"

    class Config:
        env_file = ".env"


settings = Settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",  # SQL логи только в dev
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""
    pass


async def get_db() -> AsyncSession:
    """Dependency для FastAPI — даёт сессию БД и закрывает её после запроса."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
