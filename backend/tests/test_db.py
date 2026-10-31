"""SQLite pragma configuration: WAL mode and busy timeout."""
import threading
import time

from sqlalchemy import create_engine, event, text

from app.db import SQLITE_BUSY_TIMEOUT_MS, _configure_sqlite


def test_app_engine_runs_in_wal_mode_with_busy_timeout(client):
    """The engine built by init_engine() has WAL and the busy timeout on every
    connection (the session fixture provides it)."""
    from app import db as dbmod

    with dbmod.engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert journal == "wal"
    assert busy == SQLITE_BUSY_TIMEOUT_MS


def test_two_connections_can_write_under_contention(tmp_path):
    """A second connection's write waits out a first connection's open write
    transaction (busy timeout) instead of raising 'database is locked'."""
    url = f"sqlite:///{(tmp_path / 'wal.db').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000})
    event.listen(engine, "connect", _configure_sqlite)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, tag TEXT)"))

    conn1 = engine.connect()
    conn2 = engine.connect()
    assert conn1.connection.dbapi_connection is not conn2.connection.dbapi_connection

    tx1 = conn1.begin()
    conn1.execute(text("INSERT INTO t (tag) VALUES ('first')"))
    try:
        released = threading.Event()

        def commit_first_soon():
            time.sleep(0.3)
            tx1.commit()
            released.set()

        committer = threading.Thread(target=commit_first_soon)
        committer.start()

        # while conn1 holds the write lock, conn2 writes: the busy timeout
        # makes it wait for conn1's commit instead of raising
        conn2.execute(text("INSERT INTO t (tag) VALUES ('second')"))
        conn2.commit()
        committer.join(timeout=5)
        assert released.is_set()
    finally:
        conn1.close()
        conn2.close()

    with engine.connect() as conn:
        tags = sorted(row[0] for row in conn.execute(text("SELECT tag FROM t")))
    assert tags == ["first", "second"]
