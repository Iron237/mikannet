import asyncio
from contextlib import asynccontextmanager
import socket
import sys
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uvicorn

from app.api import subscriptions
from app.clients.mikan import mikan_client
from app.database import Base, get_db
from app.main import app
from app.models import Bangumi
from mikannet_mcp.catalog import TOOLS


@asynccontextmanager
async def agent(url, *options):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "mikannet_mcp.server", "--base-url", url, *options])
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        yield session


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "test.db").as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory.begin() as db:
        db.add(Bangumi(mikan_bangumi_id=99, title="Fixture"))

    def test_db():
        with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, test_db)
    monkeypatch.setattr(subscriptions, "_poll_in_background", lambda *args: None)
    monkeypatch.setattr(mikan_client, "fetch_rss", lambda *args: [])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(0.025)
            assert server.started
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(10)
            engine.dispose()


def test_stdio_management_and_read_only(upstream):
    async def run():
        async with agent(upstream) as session:
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == set(TOOLS)
            creation = {"body": {"mikan_bangumi_id": 99, "mikan_subgroup_id": "10", "backfill": False}}
            created = await session.call_tool("create_subscription", creation)
            assert not created.isError
            sub_id = created.structuredContent["data"]["id"]
            duplicate = await session.call_tool("create_subscription", creation)
            assert duplicate.isError and "409" in duplicate.content[0].text
            result = await session.call_tool("poll_subscription", {"sub_id": sub_id})
            assert result.structuredContent["data"]["seen"] == 0
            result = await session.call_tool("update_subscription", {"sub_id": sub_id, "body": {"enabled": False}})
            assert result.structuredContent["data"]["enabled"] is False
            listing = await session.call_tool("list_subscriptions", {"page_size": 1})
            assert listing.structuredContent["pages"]["items"]["total"] == 1
            invalid = await session.call_tool("set_preferences", {"body": {"qb_password": "invalid"}})
            assert invalid.isError
            deleted = await session.call_tool("remove_subscription", {"sub_id": sub_id})
            assert deleted.structuredContent["status"] == 204
        async with agent(upstream, "--read-only") as session:
            assert all(t.annotations.readOnlyHint for t in (await session.list_tools()).tools)
            assert (await session.call_tool("create_subscription", creation)).isError
            result = await session.call_tool("get_preferences", {})
            assert "danmaku_auto_download" in result.structuredContent["data"]
            assert "qb_password" not in result.structuredContent["data"]

    asyncio.run(run())
