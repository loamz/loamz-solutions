"""The MCP tool server, through a real MCP client over SSE with a bearer token."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client

from loamz.solutions.concierge import DataConcierge, DataIndex, FolderConnector
from loamz.solutions.concierge.demo import make_demo_folder
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.mail import MailStore
from loamz.solutions.mail.concierge import MailConcierge
from loamz.solutions.mail.demo import PROFILE, DemoConnector
from loamz.solutions.mail.triage import RuleClassifier, Triager
from loamz.solutions.server import NEEDS_APPROVAL, SAFE_TOOLS, Solutions, build_app, platform_config
from loamz.solutions.trading.demo import demo_trader

TOKEN = "t0ken"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running(tmp_path):
    trader, ledger = demo_trader(tmp_path / "trader")
    mail = MailConcierge(
        DemoConnector(),
        MailStore(tmp_path / "m.sqlite"),
        Triager(RuleClassifier(PROFILE)),
        Ledger(tmp_path / "ml.sqlite"),
    )
    data = DataConcierge(
        DataIndex(tmp_path / "d.sqlite"),
        FolderConnector([make_demo_folder(tmp_path / "files")]),
        mail=mail,
    )
    service = Solutions(
        mail, trader, ledger, llm=None, kill_file=str(tmp_path / "trader" / "KILL"), data=data
    )
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(service, TOKEN), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/sse", service
    server.should_exit = True
    thread.join(timeout=5)


@asynccontextmanager
async def connect(url, token=TOKEN):
    async with sse_client(url, headers={"Authorization": f"Bearer {token}"}, timeout=5) as (
        read,
        write,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session, name, **arguments) -> str:
    result = await session.call_tool(name, arguments)
    return "".join(c.text for c in result.content)


async def test_every_tool_is_offered_and_each_is_either_vouched_for_or_needs_approval(running):
    url, _ = running
    async with connect(url) as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == set(SAFE_TOOLS) | set(NEEDS_APPROVAL)
    assert not set(SAFE_TOOLS) & set(NEEDS_APPROVAL)


async def test_a_wrong_token_is_refused(running):
    url, _ = running
    with pytest.raises(BaseException):  # noqa: B017 - the SSE client raises an exception group on a 401
        async with connect(url, token="nope"):
            pass
    assert httpx.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401


async def test_the_mail_tools_refresh_a_stale_index_by_themselves(running):
    url, service = running
    async with connect(url) as session:
        assert "Needs you today" in await call(
            session, "mail_digest"
        )  # never synced: it syncs first
    assert len(service.mail.ledger.entries(kind="mail.synced")) == 1
    async with connect(url) as session:
        await call(session, "mail_top")  # fresh now: no second sync
    assert len(service.mail.ledger.entries(kind="mail.synced")) == 1


async def test_mail_tools_sync_digest_search_and_rank(running):
    url, _ = running
    async with connect(url) as session:
        assert "triaged 15" in await call(session, "mail_sync", days=2)
        assert "Needs you today" in await call(session, "mail_digest", hours=24)
        top = await call(session, "mail_top", limit=3, at_least="urgent")
        assert "Signed contract attached" in top and top.count("\n") <= 2
        answer = await call(session, "mail_ask", question="what did Priya send about the contract?")
        assert "Signed contract attached" in answer


async def test_trading_tools_show_signals_orders_the_limits_and_the_ledger(running):
    url, service = running
    async with connect(url) as session:
        assert "mode sim" in await call(session, "trade_status")
        assert "AAA:" in await call(session, "trade_signals", symbol="aaa")
        bought = await call(session, "trade_place_order", symbol="AAA", side="buy", qty=10)
        assert "traded" in bought
        assert "AAA: 10 shares" in await call(session, "trade_status")
        assert "denied" in await call(
            session, "trade_place_order", symbol="AAA", side="buy", qty=1000
        ) and "max-notional" in await call(
            session, "trade_place_order", symbol="AAA", side="buy", qty=1000
        )
        assert "denied" in await call(
            session, "trade_place_order", symbol="TSLA", side="buy", qty=1
        )
        assert "no trade" in await call(
            session, "trade_place_order", symbol="AAA", side="short", qty=1
        )
        explained = await call(session, "trade_explain", symbol="AAA")
        assert "proposed buy 10" in explained and "order filled" in explained
        assert "orders     1" in await call(session, "trade_report")
    assert service.ledger.verify()["ok"]


async def test_the_kill_switch_tool_stops_orders_until_resumed(running):
    url, _ = running
    async with connect(url) as session:
        assert "ON" in await call(session, "trade_kill")
        assert "kill switch" in await call(
            session, "trade_place_order", symbol="AAA", side="buy", qty=1
        )
        assert "ON" in await call(session, "trade_status")
        assert "off" in await call(session, "trade_resume")
        assert "traded" in await call(session, "trade_place_order", symbol="BBB", side="buy", qty=1)


async def test_an_unconfigured_solution_says_what_to_set(tmp_path):
    service = Solutions(None, None, None)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(service, TOKEN), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        async with connect(f"http://127.0.0.1:{port}/sse") as session:
            assert "MAIL_USER" in await call(session, "mail_digest")
            assert "ALPACA_KEY_ID" in await call(session, "trade_status")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_the_platform_config_vouches_only_for_read_only_tools():
    import json

    entry = json.loads(platform_config("http://solutions:8765/sse", "tok"))[0]
    assert entry["name"] == "solutions" and entry["token"] == "tok"
    assert set(entry["safe_tools"]) == set(SAFE_TOOLS) and not set(entry["safe_tools"]) & set(
        NEEDS_APPROVAL
    )


async def test_data_tools_map_search_ask_and_suggest(running):
    url, _ = running
    async with connect(url) as session:
        mapped = await call(session, "data_inventory")
        assert (
            "## Files" in mapped and "9 readable files" in mapped and "Not readable yet" in mapped
        )
        found = await call(session, "data_search", query="indemnity cap")
        assert "northwind-call.md" in found and "northwind-summary.txt" in found
        answer = await call(session, "data_ask", question="what is the indemnity cap?")
        assert (
            "closest matches" in answer and "Sources:" in answer
        )  # no model in this test: the matches are returned
        assert "CSV files" in await call(session, "data_suggest")
        assert "No matches." == await call(session, "data_search", query="zzzz-nothing")
