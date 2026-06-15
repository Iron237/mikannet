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
    # 不用 WAL:生产是 Windows Docker 的绑定挂载(./data/mikanarr→/config),WAL 的 -wal/-shm
    # 依赖 mmap 共享内存,gRPC-FUSE/virtiofs 上不可靠 → 小事务留在 -wal 里,容器重启后丢失
    # (表现:改完 kind/设置等"成功"但重启回滚)。DELETE 回滚日志无需共享内存,绑定挂载上持久可靠。
    cur.execute("PRAGMA journal_mode=DELETE")
    cur.execute("PRAGMA synchronous=FULL")
    cur.execute("PRAGMA busy_timeout=30000")   # 单写者:写锁竞争时最多等 30s 而非立即 BUSY
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
    _migrate_bangumi_nullable_mikan()


def _migrate_columns() -> None:
    """create_all 不会给已有表加列,轻量列迁移在此处补。"""
    additions = {
        "subscription": [("pinned_guids", "JSON DEFAULT '[]'"),
                         ("blocked_guids", "JSON DEFAULT '[]'"),
                         ("episode_offset", "INTEGER DEFAULT 0"),
                         ("last_poll_ok", "BOOLEAN DEFAULT 1"),
                         ("last_poll_error", "TEXT")],
        "bangumi": [("air_weekday", "INTEGER"),
                    ("season_number", "INTEGER DEFAULT 1"),
                    ("anidb_aid", "INTEGER"),
                    ("anidb_synced_at", "DATETIME"),
                    ("kind", "VARCHAR(8) DEFAULT 'TV'"),   # SQLAlchemy 存 Enum 名:TV/MOVIE/OVA
                    ("auto_best", "BOOLEAN DEFAULT 0"),
                    ("bd_owned", "BOOLEAN DEFAULT 0")],
        "torrent": [("stalled_since", "DATETIME"),
                    ("last_progress", "FLOAT DEFAULT 0"),
                    ("progress_at", "DATETIME")],
        "episode": [("anidb_eid", "INTEGER")],
        "video_file": [("subgroup", "VARCHAR(128)"),
                       ("source", "VARCHAR(32)"),
                       ("color_depth", "VARCHAR(8)"),
                       ("hdr", "VARCHAR(16)")],
    }
    with engine.connect() as conn:
        for table, cols in additions.items():
            existing = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        conn.commit()
        _migrate_episode_type(conn)


def _migrate_bangumi_nullable_mikan() -> None:
    """bangumi.mikan_bangumi_id 由 NOT NULL 改为可空(本地导入按 bgm.tv 匹配,无蜜柑 ID)。

    SQLite 不能直接 ALTER 掉 NOT NULL,须重建表。用独立 sqlite3 连接控制事务:
    PRAGMA foreign_keys 须在事务外设置;重建保留 bangumi.id(子表 subscription/episode
    的外键引用 id,值不变 → 引用仍有效)。幂等:已可空则跳过。DELETE 回滚日志下原子安全。
    """
    import sqlite3

    con = sqlite3.connect(settings.db_path)
    con.isolation_level = None   # 关闭隐式事务,手动 BEGIN/COMMIT
    try:
        cur = con.cursor()
        col = next((r for r in cur.execute("PRAGMA table_info(bangumi)")
                    if r[1] == "mikan_bangumi_id"), None)
        if not col or int(col[3]) == 0:   # 表不存在 或 notnull 标志已为 0
            return
        ddl = cur.execute("SELECT sql FROM sqlite_master "
                          "WHERE type='table' AND name='bangumi'").fetchone()[0]
        if "mikan_bangumi_id INTEGER NOT NULL" not in ddl:
            import logging
            logging.getLogger(__name__).warning(
                "跳过 bangumi 可空迁移:未在 DDL 找到预期的 NOT NULL 子句")
            return
        new_ddl = (ddl.replace("mikan_bangumi_id INTEGER NOT NULL", "mikan_bangumi_id INTEGER")
                      .replace("CREATE TABLE bangumi", "CREATE TABLE _bangumi_new", 1))
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("BEGIN")
        cur.execute(new_ddl)
        cur.execute("INSERT INTO _bangumi_new SELECT * FROM bangumi")   # 同列序,直接整表复制
        cur.execute("DROP TABLE bangumi")
        cur.execute("ALTER TABLE _bangumi_new RENAME TO bangumi")
        cur.execute("CREATE UNIQUE INDEX ix_bangumi_mikan_bangumi_id ON bangumi (mikan_bangumi_id)")
        cur.execute("COMMIT")
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        con.close()


def _migrate_episode_type(conn) -> None:
    """剧集类型枚举重整(ADR-0003):旧名 → 新名。SQLAlchemy 以 Enum 成员名持久化。

    EP→REGULAR、SP→SPECIAL;旧作品级 OVA/MOVIE 在番剧内无干净映射,归 SPECIAL(几乎不存在)。
    番剧本身的形态由新增的 bangumi.kind 表达。幂等:只动旧值。
    """
    remap = {"EP": "REGULAR", "SP": "SPECIAL", "OVA": "SPECIAL", "MOVIE": "SPECIAL"}
    existing = {r[0] for r in conn.exec_driver_sql("SELECT DISTINCT type FROM episode")}
    for old, new in remap.items():
        if old in existing:
            conn.exec_driver_sql("UPDATE episode SET type = ? WHERE type = ?", (new, old))
    conn.commit()
