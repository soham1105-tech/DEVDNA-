"""SQLAlchemy engine and session helpers (SQLite for local development)."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def init_engine(url: str | None = None):
    """(Re)create the engine. Tests call this with a temporary database URL."""
    global engine
    url = url or settings.resolved_database_url
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url)
    SessionLocal.configure(bind=engine)
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    project_columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    finding_columns = {column["name"] for column in inspect(engine).get_columns("findings")}
    with engine.begin() as connection:
        for column, sql_type in (
            ("ast_owner", "VARCHAR(39)"),
            ("ast_repo", "VARCHAR(100)"),
            ("ast_branch", "VARCHAR(255)"),
        ):
            if column not in project_columns:
                connection.execute(text(f"ALTER TABLE projects ADD COLUMN {column} {sql_type}"))
        if "owner" not in finding_columns:
            connection.execute(text("ALTER TABLE findings ADD COLUMN owner VARCHAR(500) NOT NULL DEFAULT 'Unassigned'"))
    return engine


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
