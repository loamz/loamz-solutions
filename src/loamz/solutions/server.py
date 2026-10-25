"""The solutions as an MCP tool server, so agents on a Loamz deployment can use them.

    loamz-solutions serve --port 8765                 # needs SOLUTIONS_TOKEN
    loamz-solutions serve --demo                      # synthetic inbox and a simulated market, nothing real touched
    loamz-solutions serve --print-config              # the JSON to paste into LOAMZ_AGENTS_TOOLS__SERVERS

Read-only tools (`SAFE_TOOLS`) are what the operator can vouch for in `safe_tools`; the rest change something (an order,
a resume after a stop) and should wait for a person's approval, which is the platform's default for every tool you do not
list. Live trading cannot be reached through this server: it never carries a human approver, and a live account's every
order needs one.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loamz.solutions.kit.a2ui import Reply
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.mail import MailConcierge
from loamz.solutions.mail.triage import Priority
from loamz.solutions.trading.cards import account_card, plan_card
from loamz.solutions.trading.explain import explain
from loamz.solutions.trading.pnl import summarize
from loamz.solutions.web.images import ImageFinder

SAFE_TOOLS = [
    "mail_sync",
    "mail_digest",
    "mail_top",
    "mail_ask",
    "trade_status",
    "trade_signals",
    "trade_explain",
    "trade_report",
    "trade_kill",
    "web_images",
    "trade_ratings",
    "trade_plan",
    "trade_follow_up",
    "trade_performance",
    "data_inventory",
    "data_search",
    "data_ask",
    "data_suggest",
]
NEEDS_APPROVAL = ["trade_approve_plan", "trade_place_order", "trade_run_once", "trade_resume"]


def optional_llm() -> LLM | None:
    try:
        return LLM.from_env()
    except LLMError:
        return None


class Solutions:
    """What the tools call. Methods return plain text a model can read; one lock keeps the SQLite files single-writer."""

    def __init__(
        self,
        mail: MailConcierge | None,
        trader,
        ledger: Ledger | None,
        llm: LLM | None = None,
        kill_file: str = ".state/KILL",
        mail_max_age_minutes: int = 10,
        data=None,
        desk=None,
        images: ImageFinder | None = None,
    ) -> None:
        self.mail, self.trader, self.ledger, self.llm = mail, trader, ledger, llm
        self.data, self.desk = data, desk
        self.images = images or ImageFinder()
        self.kill_file = kill_file
        self.mail_max_age = timedelta(minutes=mail_max_age_minutes)
        self.lock = threading.Lock()

    def need_mail(self) -> MailConcierge:
        if self.mail is None:
            raise RuntimeError(
                "The Mail Concierge is not configured on this server (set MAIL_USER, MAIL_IMAP_HOST, MAIL_PASSWORD)"
            )
        return self.mail

    def need_trader(self):
        if self.trader is None:
            raise RuntimeError(
                "The Paper Trader is not configured on this server (set ALPACA_KEY_ID, ALPACA_SECRET_KEY, TRADE_SYMBOLS)"
            )
        return self.trader

    def need_desk(self):
        if self.desk is None:
            raise RuntimeError(
                "The trading desk is not configured on this server (set ALPACA_KEY_ID, ALPACA_SECRET_KEY and the Loamz "
                "Data credentials of docs/markets.md)"
            )
        return self.desk

    # ------------------------------------------------------------------ mail
    def fresh_mail(self) -> MailConcierge:
        """The Concierge, with its index refreshed if it is older than `mail_max_age`: an agent never has to know to sync."""
        mail = self.need_mail()
        last = mail.ledger.entries(kind="mail.synced", limit=1, newest_first=True)
        if not last or mail.ledger.now() - last[0].at > self.mail_max_age:
            mail.sync(days=2)
        return mail

    def mail_sync(self, days: int = 2) -> str:
        with self.lock:
            r = self.need_mail().sync(days=max(1, min(days, 30)))
        return f"Read {r.fetched} messages, {r.new} new, triaged {r.triaged}: {r.by_priority}"

    def mail_digest(self, hours: int = 24) -> str:
        with self.lock:
            return self.fresh_mail().digest(hours=max(1, min(hours, 24 * 14)))

    def mail_top(self, limit: int = 10, at_least: str = "important") -> str:
        with self.lock:
            since = datetime.now(UTC) - timedelta(days=7)
            rows = self.fresh_mail().store.top(
                since=since, at_least=Priority.parse(at_least), limit=max(1, min(limit, 50))
            )
        if not rows:
            return "Nothing at that priority in the last 7 days."
        return "\n".join(
            f"[{r['id']}] {r['priority_name']}: {r['title']} ({r['author']}), {r['reason']} → {r['action']}"
            for r in rows
        )

    def mail_ask(self, question: str) -> str:
        with self.lock:
            answer = self.fresh_mail().ask(question, self.llm)
        sources = "\n".join(
            f"- [{h['id']}] {h['at'][:10]} {h['author']}: {h['title']}" for h in answer.sources[:5]
        )
        return answer.text + (f"\n\nSources:\n{sources}" if sources else "")

    # ------------------------------------------------------------------ data
    def need_data(self):
        if self.data is None:
            raise RuntimeError(
                "The Data Concierge is not configured on this server (set DATA_FOLDERS, or run with --demo)"
            )
        return self.data

    def data_inventory(self) -> str:
        with self.lock:
            concierge = self.need_data()
            concierge.scan()
            return concierge.inventory()

    def data_search(self, query: str) -> str:
        with self.lock:
            concierge = self.need_data()
            concierge.scan()
            hits = concierge.search(query)
        if not hits:
            return "No matches."
        return "\n".join(f"[{h['where']}] {h['title']} ({h['ref']}): {h['snippet']}" for h in hits)

    def data_ask(self, question: str) -> str:
        with self.lock:
            concierge = self.need_data()
            concierge.scan()
            answer = concierge.ask(question)
        sources = "\n".join(f"- [{h['ref']}] {h['title']}" for h in answer.sources[:5])
        return answer.text + (f"\n\nSources:\n{sources}" if sources else "")

    def data_suggest(self) -> str:
        with self.lock:
            concierge = self.need_data()
            concierge.scan()
            return concierge.suggest()

    # ------------------------------------------------------------------ web
    def web_images(self, query: str, limit: int = 6) -> str:
        return self.images.describe(query, limit)

    # ------------------------------------------------------------------ trading
    def trade_status(self) -> Reply:
        with self.lock:
            broker = self.desk.broker if self.desk else self.need_trader().broker
            account, positions = broker.account(), broker.positions()
            ledgers = [x for x in (self.ledger, self.desk.ledger if self.desk else None) if x]
            killed = Path(self.kill_file).exists()
            intact = all(x.verify()["ok"] for x in ledgers)
        lines = [
            f"mode {broker.mode}; equity {account.equity:,.2f}; cash {account.cash:,.2f}; today {account.day_pnl:+,.2f}",
            f"kill switch {'ON' if killed else 'off'}; ledger {'intact' if intact else 'ALTERED'}",
        ]
        lines += [
            f"{p.symbol}: {p.qty:g} shares, average {p.avg_price:,.2f}, now {p.market_price:,.2f}"
            for p in positions
        ]
        card = account_card(
            broker.mode, account.equity, account.cash, account.day_pnl, positions, killed, intact
        )
        return Reply.shown("\n".join(lines), card)

    def trade_signals(self, symbol: str) -> str:
        """What the model thinks of a symbol right now. Nothing is traded."""
        with self.lock:
            t = self.need_trader()
            symbol = symbol.upper()
            signal = t.model.predict(symbol, t.broker.bars(symbol, t.config.lookback))
        return f"{symbol}: {signal.side} at {signal.confidence:.0%} confidence, price {signal.price}. {signal.rationale}"

    def trade_explain(self, symbol: str) -> str:
        with self.lock:
            self.need_trader()
            return explain(self.ledger, symbol.upper(), self.llm)

    def trade_report(self) -> str:
        with self.lock:
            self.need_trader()
            return summarize(self.ledger).render()

    def trade_kill(self) -> str:
        path = Path(self.kill_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stopped by an agent or a person\n")
        return "Kill switch ON. Nothing will trade until it is resumed."

    def trade_resume(self) -> str:
        Path(self.kill_file).unlink(missing_ok=True)
        return "Kill switch off. Trading may continue."

    def trade_run_once(self) -> str:
        with self.lock:
            return self.need_trader().run_once().render()

    def trade_ratings(self, at_least: str = "buy", symbol: str = "") -> Reply:
        with self.lock:
            return self.need_desk().ratings(at_least, symbol)

    def trade_plan(self) -> Reply:
        with self.lock:
            plan = self.need_desk().plan()
        note = (
            f"\n\nThe person is shown this plan as a card with an Approve button. Do not repeat the orders: say in one or two "
            f"sentences what stands out (the biggest changes, the risk), then ask whether to approve plan {plan.id}."
        )
        return Reply(plan.render() + (note if plan.orders else ""), plan_card(plan))

    def trade_approve_plan(self, plan_id: str) -> str:
        with self.lock:
            return self.need_desk().approve(plan_id.strip())

    def trade_follow_up(self) -> str:
        with self.lock:
            return self.need_desk().follow_up()

    def trade_performance(self, days: int = 30) -> Reply:
        with self.lock:
            return self.need_desk().performance(max(2, min(days, 365)))

    def trade_place_order(self, symbol: str, side: str, qty: float) -> str:
        with self.lock:
            r = self.need_trader().place_manual(symbol, side.lower(), qty)
        return f"{r.symbol}: {r.status}. {r.detail}"


def build_app(service: Solutions, token: str):
    """The MCP server as an ASGI app (SSE), refusing anything without the bearer token."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent

    mcp = FastMCP("solutions", host="0.0.0.0", port=int(os.environ.get("PORT", "8765")))

    def register(fn):
        async def run(**kwargs: Any) -> CallToolResult:
            try:
                answer = await asyncio.to_thread(fn, **kwargs)
            except Exception as error:  # noqa: BLE001 - the model gets the reason, not a stack trace
                answer = f"Error: {error}"
            # The text is for the model; A2UI messages, when there are any, are for the person's screen (kit/a2ui.py).
            reply = answer if isinstance(answer, Reply) else Reply(str(answer))
            return CallToolResult(
                content=[TextContent(type="text", text=reply.text)],
                structuredContent={"a2ui": reply.a2ui} if reply.a2ui else None,
            )

        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        run.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "return"} | {
            "return": CallToolResult
        }
        import inspect

        run.__signature__ = inspect.signature(fn).replace(return_annotation=CallToolResult)  # type: ignore[attr-defined]
        mcp.tool()(run)

    docs = {
        "mail_sync": "Read the mailbox (read-only) and rank any new messages by priority.",
        "mail_digest": "The mail digest: what needs the owner today, this week, and what can be ignored.",
        "mail_top": "The highest-priority recent messages, each with a reason and a suggested action.",
        "mail_ask": "Answer a question about the owner's mail, citing the messages used.",
        "trade_status": "Account equity, cash, today's profit and loss, positions, the kill switch and ledger state.",
        "trade_signals": "What the signal model thinks of one symbol right now. Does not trade.",
        "trade_explain": "Explain, from the ledger, what the trader saw, decided and did for one symbol.",
        "trade_report": "Profit and loss, win rate and drawdown from the ledger.",
        "data_inventory": "A map of where the owner's data lives (files, mail, Loamz) and what can be done with each.",
        "data_search": "Find things across the owner's files, mail and Loamz data by words.",
        "data_ask": "Answer a question using the owner's files, mail and Loamz data, citing what was used.",
        "data_suggest": "Useful things the owner could do with the data they have.",
        "trade_ratings": "The strategy's rating of each stock (strong buy to strong sell) with the factors behind it, from the "
        "last close. at_least: strong buy, buy, hold, sell or strong sell; or one symbol.",
        "trade_plan": "Propose the rebalance for the next open: each order with its reason, limit price, stop and dollars at "
        "risk, and its plan id. Records the plan; sends nothing.",
        "trade_approve_plan": "Send the proposed plan with this id to the broker, exactly as shown: sells first, buys while cash "
        "covers them. Only after the owner has seen the plan and agreed.",
        "trade_follow_up": "Check the approved plan's orders: record fills at their real prices and send the buys that were "
        "waiting for the sells' cash. Sends nothing that was not approved.",
        "trade_performance": "The account's return and worst drop over the last `days`, next to SPY's.",
        "web_images": "Search the web for pictures. Returns, for each, an address to put in an Image of a card or a rich answer and the "
        "page it came from. Use for a header picture or a carousel; never invent picture addresses.",
        "trade_kill": "Stop all trading immediately (the kill switch). Always safe.",
        "trade_resume": "Turn the kill switch off so trading may continue.",
        "trade_run_once": "Run one trading pass over all symbols: the model decides and every risk limit applies.",
        "trade_place_order": "Place one paper order (side buy or sell, qty shares). Every risk limit applies, and a refusal says why.",
    }
    for name, doc in docs.items():
        fn = getattr(service, name)
        fn.__func__.__doc__ = doc
        register(fn)

    class Bearer:
        def __init__(self, app) -> None:
            self.app = app

        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] == "http":
                given = dict(scope["headers"]).get(b"authorization", b"").decode()
                if given != f"Bearer {token}":
                    await send({"type": "http.response.start", "status": 401, "headers": []})
                    await send({"type": "http.response.body", "body": b"unauthorized"})
                    return
            await self.app(scope, receive, send)

    return Bearer(mcp.sse_app())


def platform_config(url: str, token: str) -> str:
    return json.dumps([{"name": "solutions", "url": url, "token": token, "safe_tools": SAFE_TOOLS}])


def from_env(demo: bool = False) -> Solutions:
    """Build the service from the environment: each solution is on only when it is configured."""
    from loamz.solutions import cli
    from loamz.solutions.trading.demo import demo_trader

    state = cli.state_dir()
    mail = trader = ledger = None
    if demo:
        mail = cli.build_mail(demo=True)
        trader, ledger = demo_trader(state / "demo-trader")
    else:
        if os.environ.get("MAIL_USER") and os.environ.get("MAIL_PASSWORD"):
            mail = cli.build_mail()
        if os.environ.get("ALPACA_KEY_ID") and os.environ.get("TRADE_SYMBOLS"):
            from loamz.solutions.kit.guard import refuse
            from loamz.solutions.trading import cli as trading
            from loamz.solutions.trading.engine import Trader

            ledger = Ledger(trading.state_dir() / "ledger.sqlite")
            symbols = {
                x.strip().upper() for x in os.environ["TRADE_SYMBOLS"].split(",") if x.strip()
            }
            # No human is at this terminal: a live account's orders (which need approval) are refused by construction.
            trader = Trader(
                trading.broker_from_env(),
                trading.model(),
                trading.trader_config(symbols),
                ledger,
                approver=refuse,
            )
    desk = None if demo else build_desk(os.environ)
    kill = trader.config.risk.kill_file if trader else ".state/KILL"
    concierge = None
    if demo or os.environ.get("DATA_FOLDERS"):
        concierge = cli.build_data(demo=demo)
        concierge.llm = optional_llm()
    return Solutions(
        mail, trader, ledger, optional_llm(), kill_file=kill, data=concierge, desk=desk
    )


def build_desk(env):
    """The desk trades the strategy on the Alpaca account, reading prices from Loamz Data; off unless both are configured."""
    from loamz.solutions.markets import cli as markets

    if not markets.configured(env) or env.get("TRADE_STRATEGY", "momentum-trend") == "off":
        return None
    from loamz.solutions.trading import cli as trading
    from loamz.solutions.trading.desk import STRATEGIES, Desk, PanelSource
    from loamz.solutions.trading.portfolio import PortfolioRules

    name = env.get("TRADE_STRATEGY", "momentum-trend")
    if name not in STRATEGIES:
        raise RuntimeError(f"TRADE_STRATEGY must be one of {', '.join(STRATEGIES)} or off")
    idle = env.get("TRADE_IDLE", "SPY")
    data, warehouse = markets.build(env)
    return Desk(
        trading.broker_from_env(),
        PanelSource(data, warehouse),
        Ledger(trading.state_dir() / "desk.sqlite"),
        strategy=STRATEGIES[name](),
        rules=PortfolioRules(idle_in=None if idle.lower() == "none" else idle.upper()),
        kill_file=trading.risk_config(set()).kill_file,
    )
