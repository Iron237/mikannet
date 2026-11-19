"""本地导入必须在整份文件写完后才公布目标名。"""
import errno
import stat
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.models import Bangumi, Torrent, VideoFile
from app.services import local_import


def cross_volume(monkeypatch, source):
    rename = local_import._rename_new

    def rename_across_volumes(src, dest):
        if src == source:
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(src, dest)

    monkeypatch.setattr(local_import, "_rename_new", rename_across_volumes)


def test_interrupted_copy_can_retry_without_partial_media(tmp_path, monkeypatch):
    source, target = tmp_path / "source.mkv", tmp_path / "final.mkv"
    source.write_bytes(b"full episode")
    new_file_mode = stat.S_IMODE(source.stat().st_mode)
    cross_volume(monkeypatch, source)

    def fail_copy(fin, fout, _length):
        fout.write(fin.read(3))
        assert not target.exists()
        raise OSError(errno.EIO, "interrupted")

    with monkeypatch.context() as failing:
        failing.setattr(local_import.shutil, "copyfileobj", fail_copy)
        with pytest.raises(OSError):
            local_import._safe_move(source, target)
    assert list(tmp_path.iterdir()) == [source]
    local_import._safe_move(source, target)
    assert target.read_bytes() == b"full episode"
    assert stat.S_IMODE(target.stat().st_mode) == new_file_mode
    assert list(tmp_path.iterdir()) == [target]


def test_target_created_during_copy_is_preserved(tmp_path, monkeypatch):
    source, target = tmp_path / "source.mkv", tmp_path / "final.mkv"
    source.write_bytes(b"first source")
    cross_volume(monkeypatch, source)

    def other_import_wins(fin, fout, _length):
        target.write_bytes(b"concurrent source")
        fout.write(fin.read())

    monkeypatch.setattr(local_import.shutil, "copyfileobj", other_import_wins)
    with pytest.raises(FileExistsError):
        local_import._safe_move(source, target)
    assert source.read_bytes() == b"first source"
    assert target.read_bytes() == b"concurrent source"
    assert len(list(tmp_path.iterdir())) == 2


def test_staging_name_collision_preserves_other_copy(tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    cross_volume(monkeypatch, source)
    identifier = UUID(int=42)
    monkeypatch.setattr(local_import, "uuid4", lambda: identifier)
    busy = tmp_path / f".mikannet-import-{identifier.hex}.part"
    busy.write_bytes(b"concurrent import in progress")
    with pytest.raises(FileExistsError):
        local_import._safe_move(source, tmp_path / "target.mkv")
    assert busy.read_bytes() == b"concurrent import in progress"
    assert source.read_bytes() == b"source"


def test_existing_unverified_destination_is_not_registered(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(settings, "download_root_local", root)
    source = tmp_path / "01.mkv"
    source.write_bytes(b"complete")
    target = root / "番" / source.name
    target.parent.mkdir()
    target.write_bytes(b"part")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bangumi(title="番", bgmtv_subject_id=1))
        db.commit()

        @contextmanager
        def session():
            yield db
            db.commit()

        monkeypatch.setattr(local_import, "db_session", session)
        group = {"bgm": {"bgmtv_subject_id": 1}, "files": [str(source)]}
        assert "1 个失败" in local_import._import_group(group)
        assert db.scalar(select(VideoFile.id)) is None
        assert db.scalar(select(Torrent.id)) is None
    assert target.read_bytes() == b"part" and source.read_bytes() == b"complete"
    engine.dispose()


def test_concurrent_import_requests_reserve_single_worker(monkeypatch):
    starts = []
    monkeypatch.setattr(local_import, "state", {"running": False})
    requests = 6
    barrier = threading.Barrier(requests)

    def worker():
        barrier.wait()
        return local_import.start_import([])

    class DeferredThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            starts.append(1)

    # 只替换模块引用，不影响 ThreadPoolExecutor 创建并发请求线程。
    from types import SimpleNamespace
    monkeypatch.setattr(local_import, "threading", SimpleNamespace(Thread=DeferredThread))
    with ThreadPoolExecutor(requests) as pool:
        responses = [job.result() for job in [pool.submit(worker) for _ in range(requests)]]
    assert responses.count(True) == 1 and starts == [1]
