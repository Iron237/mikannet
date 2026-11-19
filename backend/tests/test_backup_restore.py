import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.backup import export_data, router
from app.config import settings
from app.database import Base, get_db
from app.models import Bangumi, NotificationConfig, Setting
from app.services import backup


@pytest.fixture()
def library():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bangumi(id=1, title="当前库"))
        db.add(NotificationConfig(channel="telegram", enabled=False, credentials={}, events={}))
        db.commit()
    app = FastAPI()
    app.include_router(router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    async def send(method, url, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.request(method, url, **kwargs)

    def request(method, url, **kwargs):
        return asyncio.run(send(method, url, **kwargs))

    yield request, engine
    engine.dispose()


def test_preview_and_restore(library):
    request, engine = library
    data = request("GET", "/api/backup/export").json()
    data["tables"]["bangumi"][0]["title"] = "备份中的番剧"
    result = request("POST", "/api/backup/preview", json=data)
    assert result.status_code == 200
    assert result.json()["incoming"]["bangumi"] == result.json()["current"]["bangumi"] == 1
    assert "notification_config" not in result.json()["incoming"]
    with Session(engine) as db:
        assert db.get(Bangumi, 1).title == "当前库"
    restored = request("POST", "/api/backup/import", json=data)
    assert restored.status_code == 200
    assert restored.json()["imported"]["bangumi"] == 1
    with Session(engine) as db:
        assert db.get(Bangumi, 1).title == "备份中的番剧"
        assert db.scalar(select(NotificationConfig.channel)) == "telegram"


@pytest.mark.parametrize("problem", ["missing_table", "malformed_table", "unsupported_version",
                                     "count_mismatch", "duplicate_id", "missing_parent", "invalid_enum",
                                     "missing_primary_key", "invalid_json_shape"])
def test_invalid_backup_preserves_library(library, problem):
    request, engine = library
    data = request("GET", "/api/backup/export").json()
    data["tables"]["bangumi"][0]["title"] = "不可写入"
    if problem == "missing_table":
        del data["tables"]["episode"]
        del data["counts"]["episode"]
    elif problem == "malformed_table":
        data["tables"]["episode"] = {}
    elif problem == "unsupported_version":
        data["version"] = 10
    elif problem == "count_mismatch":
        data["tables"]["bangumi"] = []
    elif problem == "duplicate_id":
        data["tables"]["bangumi"] *= 2
        data["counts"]["bangumi"] = 2
    elif problem == "missing_parent":
        data["tables"]["episode"] = [{"id": 1, "bangumi_id": 123}]
        data["counts"]["episode"] = 1
    elif problem == "invalid_enum":
        data["tables"]["bangumi"][0]["kind"] = "invalid-enum"
    elif problem == "invalid_json_shape":
        data["tables"]["notification_config"] = [{"id": 1, "channel": "telegram", "events": []}]
        data["counts"]["notification_config"] = 1
    else:
        del data["tables"]["bangumi"][0]["id"]
    for action in ("preview", "import"):
        assert request("POST", "/api/backup/" + action, json=data).status_code == 400
    with Session(engine) as db:
        assert db.get(Bangumi, 1).title == "当前库"


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_backup_with_optional_tables(library, monkeypatch, version):
    request, engine = library
    monkeypatch.setattr(settings, "nfo_enabled", True)
    data = request("GET", "/api/backup/export").json()
    data["version"] = version
    del data["tables"]["auto_scan_log"], data["counts"]["auto_scan_log"]
    b = data["tables"]["bangumi"][0]
    del b["auto_download_disabled"]
    b["bd_owned"] = True
    changes = {"nfo_enabled": False, "qb_password": "fixture-password"}
    if version == 1:
        data["format"] = "mikanarr-backup"
        data["tables"]["setting"] = [{"key": k, "value": {"v": v}} for k, v in changes.items()]
        data["counts"]["setting"] = len(changes)
        del data["counts"]["settings"]
    else:
        data["settings"] = changes
        data["counts"]["settings"] = len(changes)
    preview = request("POST", "/api/backup/preview", json=data)
    assert preview.status_code == 200
    assert preview.json()["settings_count"] == 1
    assert "fixture-password" not in preview.text
    assert settings.nfo_enabled is True
    assert request("POST", "/api/backup/import", json=data).status_code == 200
    assert settings.nfo_enabled is False
    with Session(engine) as db:
        assert db.get(Setting, "qb_password") is None
        assert db.get(Setting, "nfo_enabled").value == {"v": False}
        assert db.get(Bangumi, 1).auto_download_disabled is True


def test_setting_write_failure_rolls_back_library(library, monkeypatch):
    request, engine = library
    data = request("GET", "/api/backup/export").json()
    data["tables"]["bangumi"][0]["title"] = "不可写入"

    def fail_write(db, changes):
        raise RuntimeError("simulated setting failure")

    monkeypatch.setattr(backup.settings_service, "persist", fail_write)
    response = request("POST", "/api/backup/import", json=data)
    assert response.status_code == 500
    with Session(engine) as db:
        assert db.get(Bangumi, 1).title == "当前库"


def test_export_uses_one_read_snapshot_during_background_writes(tmp_path):
    import json
    from app.models import Episode

    engine = create_engine(f"sqlite:///{tmp_path / 'export.sqlite'}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            Base.metadata.create_all(conn)
        inserted = False

        @event.listens_for(engine, "after_cursor_execute")
        def add_during_export(conn, cursor, statement, params, context, many):
            nonlocal inserted
            if inserted or "FROM bangumi" not in statement:
                return
            inserted = True
            with Session(engine) as writer:
                writer.add(Bangumi(id=1, title="另一事务"))
                writer.flush()
                writer.add(Episode(id=1, bangumi_id=1, number=1))
                writer.commit()

        with Session(engine) as reader:
            data = json.loads(export_data(False, reader).body)
        assert inserted
        assert data["tables"]["bangumi"] == data["tables"]["episode"] == []
        with Session(engine) as db:
            assert db.get(Episode, 1).bangumi_id == 1
    finally:
        engine.dispose()
