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


def test_init_engine_twice_disposes_old_engine_and_keeps_data(tmp_path):
    """Re-initialising must not leak the old engine's connections and must not
    lose data written through the previous engine.

    init_engine rebinds the global SessionLocal, so the session's real engine
    is restored afterwards — later tests would otherwise read this tmp DB.
    """
    from sqlalchemy import text

    from app import db as dbmod

    original_url = str(dbmod.engine.url)
    url = f"sqlite:///{(tmp_path / 'twice.db').as_posix()}"
    try:
        first = dbmod.init_engine(url)
        with first.begin() as conn:
            conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY, v TEXT)"))
            conn.execute(text("INSERT INTO marker (v) VALUES ('kept')"))
        old_engine = first

        disposed = []
        monkey_old = old_engine.dispose
        old_engine.dispose = lambda: disposed.append(True) or monkey_old()

        second = dbmod.init_engine(url)
        assert disposed == [True]          # the old pool was released
        assert second is not old_engine    # a fresh engine was created
        with second.connect() as conn:     # data survives on the new engine
            assert conn.execute(text("SELECT v FROM marker")).scalar() == "kept"
    finally:
        dbmod.init_engine(original_url)


def test_schema_version_tracks_applied_migrations(tmp_path):
    """init_engine records applied migrations in schema_version and a second
    init does not re-run them."""
    import sqlite3

    from app import db as dbmod

    original_url = str(dbmod.engine.url)
    url = f"sqlite:///{(tmp_path / 'mig.db').as_posix()}"
    try:
        dbmod.init_engine(url)
        dbmod.init_engine(url)  # second pass must be a clean no-op
        raw = sqlite3.connect(tmp_path / "mig.db")
        try:
            versions = [r[0] for r in raw.execute("SELECT version FROM schema_version ORDER BY version")]
            assert versions == sorted({v for v, _, _ in dbmod.MIGRATIONS})
            count = raw.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            assert count == len(versions)
        finally:
            raw.close()
    finally:
        dbmod.init_engine(original_url)


def test_parse_junit_guards_non_numeric_time(tmp_path):
    """A junit file with a non-numeric time attribute parses with time 0
    instead of raising ValueError."""
    from app.experiments.runner import parse_junit

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite name="t">'
        '<testcase classname="t" name="ok" time="0.5"/>'
        '<testcase classname="t" name="broken_time" time="not-a-number"/>'
        '<testcase classname="t" name="no_time"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    results = parse_junit(xml)
    assert results["t::ok"]["time"] == 0.5
    assert results["t::broken_time"]["time"] == 0.0  # guarded, not a crash
    assert results["t::no_time"]["time"] == 0.0
