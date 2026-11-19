from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.tasks import TaskSyncRequest, sync_tasks
from app.database import Base
from app.models import Bangumi, Subscription, Torrent, TorrentStatus


def test_sync_reconciles_queue_and_new_arrivals_without_downloader(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        show = Bangumi(title="番剧")
        sub = Subscription(bangumi=show, mikan_subgroup_id="1", save_path="/downloads")
        db.add(sub)

        def add(guid, status):
            row = Torrent(subscription=sub, guid=guid, title_raw=guid, torrent_url="", info_hash=guid,
                          status=status)
            db.add(row)
            db.flush()
            return row

        old = add("history", TorrentStatus.ARCHIVED)
        active = add("active", TorrentStatus.COMPLETED)
        hidden = add("skipped", TorrentStatus.DOWNLOADING)
        gone = add("removed", TorrentStatus.SUBMIT_FAILED)
        gone_id = gone.id
        db.commit()
        calls = []

        def offline():
            calls.append(1)
            raise OSError("offline")

        monkeypatch.setattr("app.api.tasks.downloader.list_tasks", offline)
        full = sync_tasks(TaskSyncRequest(), db)
        assert full["live_available"] is False
        assert len(full["tasks"]) == 4
        active.status = TorrentStatus.ARCHIVED
        hidden.status = TorrentStatus.SKIPPED
        failure = add("new failure", TorrentStatus.SUBMIT_FAILED)
        failure.error_message = "测试错误"
        fast = add("fast archive", TorrentStatus.ARCHIVED)
        db.delete(gone)
        db.commit()

        delta = sync_tasks(TaskSyncRequest(after_id=full["cursor"],
                                           watching=[active.id, hidden.id, gone_id]), db)
        rows = {row.id: row for row in delta["tasks"]}
        assert rows[active.id].status == "archived"
        assert rows[failure.id].error_message == "测试错误"
        assert rows[fast.id].bangumi_title == "番剧"
        assert old.id not in rows
        assert set(delta["removed"]) == {hidden.id, gone_id}
        assert delta["cursor"] == fast.id
        assert not delta["full"]
        assert calls == [1]

        reset = sync_tasks(TaskSyncRequest(after_id=fast.id + 20), db)
        assert reset["full"]
        assert {row.id for row in reset["tasks"]} == {active.id, failure.id, fast.id, old.id}
