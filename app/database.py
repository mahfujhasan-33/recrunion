from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


def create_database_engine(database_url: str) -> Engine:
    """Create the shared SQLAlchemy engine without opening a connection."""

    connect_args = {"connect_timeout": 3} if database_url.startswith("postgresql") else {}
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


class Base(DeclarativeBase):
    """Declarative base for RecrUnion persistence models."""


def create_session_factory(database_engine: Engine) -> sessionmaker[Session]:
    """Create a session factory with explicit transaction ownership."""

    return sessionmaker(bind=database_engine, expire_on_commit=False)


engine = create_database_engine(get_settings().database_url)
SessionFactory = create_session_factory(engine)


def get_db_session() -> Iterator[Session]:
    """Provide one SQLAlchemy session per request."""

    with SessionFactory() as session:
        yield session
