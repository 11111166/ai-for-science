from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def business_store_health() -> dict[str, object]:
    """Describe the authoritative store without exposing its filesystem path."""
    backend = engine.url.get_backend_name()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "available": True,
            "database": "SQLite" if backend == "sqlite" else backend,
            "role": "authoritative_business_store",
            "authoritative": True,
            "contains": ["projects", "jobs", "result_references", "audit_events"],
        }
    except Exception as exc:
        return {
            "available": False,
            "database": "SQLite" if backend == "sqlite" else backend,
            "role": "authoritative_business_store",
            "authoritative": True,
            "error": type(exc).__name__,
        }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

