"""SQLite engine + session helpers (SQLModel).

- File databases get WAL journaling and a busy timeout.
- TK_DB_PATH=":memory:" uses one shared connection (StaticPool) so tests see a single database.
- Tables are created lazily on first use (`init_db()` is idempotent), because app.main's lifespan is
  owned elsewhere and must keep booting even without this module.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as DbSession
from sqlmodel import SQLModel, create_engine

from app.config import get_settings

log = logging.getLogger("turnkeeper.db")

_init_lock = threading.Lock()
_initialized = False


def _make_engine(db_path: str) -> Engine:
    if db_path in ("", ":memory:"):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    journal_mode = sqlite_journal_mode(db_path)

    @event.listens_for(eng, "connect")
    def _set_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - exercised only with file DBs
        cur = dbapi_conn.cursor()
        try:
            cur.execute(f"PRAGMA journal_mode={journal_mode}")
            cur.execute("PRAGMA synchronous=NORMAL" if journal_mode == "WAL" else "PRAGMA synchronous=FULL")
            cur.execute("PRAGMA busy_timeout=30000")
        finally:
            cur.close()

    return eng


def sqlite_journal_mode(db_path: str) -> str:
    """WAL needs shared memory on a local disk. Azure App Service's persistent /home is a network share where
    WAL is unsafe (https://www.sqlite.org/wal.html), so databases under /home use the rollback journal.
    TK_SQLITE_JOURNAL (WAL|DELETE) overrides the choice."""
    override = os.environ.get("TK_SQLITE_JOURNAL", "").strip().upper()
    if override in ("WAL", "DELETE"):
        return override
    raw = db_path.replace("\\", "/")
    on_home = raw.startswith("/home/") or (
        os.name == "posix" and os.path.abspath(db_path).startswith("/home/")
    )
    return "DELETE" if on_home else "WAL"


engine: Engine = _make_engine(get_settings().db_path)


def upgrade_schema(eng: Engine | None = None) -> list[str]:
    """Add columns the models gained since the file database was created (SQLite ALTER TABLE ADD COLUMN).

    Additive only: a new column is added nullable with its compiled type and no constraints. Dropped or
    retyped columns are not handled (there is no schema migration tool in this project yet). Returns the
    "table.column" names that were added.
    """
    import app.models  # noqa: F401

    eng = eng or engine
    added: list[str] = []
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            present = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')}
            if not present:  # create_all handles new tables
                continue
            for col in table.columns:
                if col.name in present:
                    continue
                col_type = col.type.compile(dialect=eng.dialect)
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}')
                added.append(f"{table.name}.{col.name}")
    if added:
        log.info("database schema upgraded: added %s", ", ".join(added))
    return added


def init_db() -> None:
    """Create all tables once per process and add any missing columns (safe to call repeatedly)."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        import app.models  # noqa: F401  (registers the tables on SQLModel.metadata)

        SQLModel.metadata.create_all(engine)
        upgrade_schema(engine)
        _initialized = True
        log.info("database ready at %s", get_settings().db_path)


def reset_db() -> None:
    """Drop and recreate every table. Used by tests for isolation."""
    global _initialized
    import app.models  # noqa: F401

    with _init_lock:
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        _initialized = True


@contextmanager
def session_scope() -> Iterator[DbSession]:
    """Transactional scope: commits on success, rolls back on error, never expires loaded objects."""
    init_db()
    db = DbSession(engine, expire_on_commit=False)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_session() -> Iterator[DbSession]:
    """FastAPI dependency: `db: DbSession = Depends(get_session)`."""
    init_db()
    with DbSession(engine, expire_on_commit=False) as db:
        yield db
