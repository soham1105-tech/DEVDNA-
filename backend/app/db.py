"""SQLAlchemy engine and session helpers (SQLite for local development)."""
from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# SQLite writers lock the whole database; without these settings a write from
# an API request while a background task writes raises "database is locked".
# WAL lets readers proceed during a write, and the busy timeout makes a
# blocked writer wait (30s) instead of failing immediately.
SQLITE_BUSY_TIMEOUT_MS = 30_000


def _configure_sqlite(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


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
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000})
        event.listen(engine, "connect", _configure_sqlite)
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
