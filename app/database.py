from sqlalchemy import Engine, create_engine

from app.config import get_settings


def create_database_engine(database_url: str) -> Engine:
    """Create the shared SQLAlchemy engine without opening a connection."""

    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


engine = create_database_engine(get_settings().database_url)
