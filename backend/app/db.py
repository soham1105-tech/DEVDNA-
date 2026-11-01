"""SQLAlchemy engine, schema migrations, and session helpers (SQLite locally)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

log = logging.getLogger("devdna.db")

# SQLite writers lock the whole database; without these settings a write from
# an API request while a background task writes raises "database is locked".
# WAL lets readers proceed during a write, and the busy timeout makes a
# blocked writer wait (30s) instead of failing immediately.
SQLITE_BUSY_TIMEOUT_MS = 30_000


def _configure_sqlite(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    # WAL can fail on read-only databases or unusual filesystems; the pragma
    # result is checked explicitly so we degrade to the default journal mode
    # with a warning instead of guessing.
    try:
        cursor.execute("PRAGMA journal_mode = WAL")
        mode = cursor.fetchone()
        if not mode or str(mode[0]).lower() != "wal":
            log.warning("SQLite journal_mode is %s (WAL not active); concurrent access may be slower",
                        mode[0] if mode else "unknown")
    except Exception as exc:  # pragma: no cover - depends on the sqlite build
        log.warning("Could not enable WAL mode (%s); using the default journal mode", exc)
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def _add_project_columns(connection, project_columns: set[str]) -> None:
    for column, sql_type in (
        ("ast_owner", "VARCHAR(39)"),
        ("ast_repo", "VARCHAR(100)"),
        ("ast_branch", "VARCHAR(255)"),
    ):
        if column not in project_columns:
            connection.execute(text(f"ALTER TABLE projects ADD COLUMN {column} {sql_type}"))


def _add_finding_owner_column(connection, finding_columns: set[str]) -> None:
    if "owner" not in finding_columns:
        connection.execute(text("ALTER TABLE findings ADD COLUMN owner VARCHAR(500) NOT NULL DEFAULT 'Unassigned'"))


def _dedup_outcomes_and_index(connection) -> None:
    # re-runs of an experiment must not double-count in calibration: dedup
    # legacy duplicate outcome rows, then enforce uniqueness at the schema
    # level (SQLite treats NULLs as distinct, so seeded rows without an
    # experiment_id are unaffected)
    connection.execute(text(
        "DELETE FROM prediction_outcomes WHERE id NOT IN "
        "(SELECT MIN(id) FROM prediction_outcomes "
        "GROUP BY experiment_id, test_nodeid)"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_outcomes_experiment_nodeid "
        "ON prediction_outcomes (experiment_id, test_nodeid)"
    ))


# Ordered, one-shot migrations. Each entry runs at most once per database,
# tracked in the schema_version table. Column-presence checks inside a
# migration keep them idempotent for databases created by create_all (which
# already has the newest shape).
MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "projects: ast_* columns", lambda c: _add_project_columns(c, {column["name"] for column in inspect(c).get_columns("projects")})),
    (2, "findings: owner column", lambda c: _add_finding_owner_column(c, {column["name"] for column in inspect(c).get_columns("findings")})),
    (3, "prediction_outcomes: dedup + unique (experiment_id, test_nodeid)", _dedup_outcomes_and_index),
]


def _migrate(connection) -> int:
    """Apply pending migrations in order; returns how many ran."""
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, "
        "description TEXT NOT NULL, "
        "applied_at TEXT NOT NULL)"
    ))
    applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_version"))}
    ran = 0
    for version, description, step in MIGRATIONS:
        if version in applied:
            continue
        step(connection)
        connection.execute(text("INSERT INTO schema_version (version, description, applied_at) VALUES (:v, :d, :t)"),
                           {"v": version, "d": description,
                            "t": datetime.now(timezone.utc).isoformat()})
        log.info("Applied migration %d: %s", version, description)
        ran += 1
    return ran


def init_engine(url: str | None = None):
    """(Re)create the engine. Tests call this with a temporary database URL."""
    global engine
    if engine is not None:
        engine.dispose()  # release the old pool; re-init must not leak connections
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
    with engine.begin() as connection:
        _migrate(connection)
    return engine


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
