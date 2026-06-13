"""SQLAlchemy 引擎与会话(SQLite WAL,单进程单写者)。"""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Iterator[Session]:
    """后台任务(scheduler/engine)用。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """建表(开发期 create_all;首个发布版前冻结为 alembic 迁移)。"""
    from app import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    _migrate_columns()


def _migrate_columns() -> None:
    """create_all 不会给已有表加列,轻量列迁移在此处补。"""
    additions = {
        "subscription": [("pinned_guids", "JSON DEFAULT '[]'"),
                         ("blocked_guids", "JSON DEFAULT '[]'"),
                         ("episode_offset", "INTEGER DEFAULT 0"),
                         ("last_poll_ok", "BOOLEAN DEFAULT 1"),
                         ("last_poll_error", "TEXT")],
        "bangumi": [("air_weekday", "INTEGER"),
                    ("season_number", "INTEGER DEFAULT 1")],
        "torrent": [("stalled_since", "DATETIME")],
        "video_file": [("subgroup", "VARCHAR(128)"),
                       ("source", "VARCHAR(32)")],
    }
    with engine.connect() as conn:
        for table, cols in additions.items():
            existing = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        conn.commit()
