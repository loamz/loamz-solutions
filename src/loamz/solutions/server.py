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

from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.mail import MailConcierge
from loamz.solutions.mail.triage import Priority
from loamz.solutions.trading.explain import explain
from loamz.solutions.trading.pnl import summarize

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
]
NEEDS_APPROVAL = ["trade_place_order", "trade_run_once", "trade_resume"]


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
    ) -> None:
        self.mail, self.trader, self.ledger, self.llm, self.kill_file = (
            mail,
            trader,
            ledger,
            llm,
            kill_file,
        )
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

    # ------------------------------------------------------------------ mail
    def mail_sync(self, days: int = 2) -> str:
        with self.lock:
            r = self.need_mail().sync(days=max(1, min(days, 30)))
        return f"Read {r.fetched} messages, {r.new} new, triaged {r.triaged}: {r.by_priority}"

    def mail_digest(self, hours: int = 24) -> str:
        with self.lock:
            return self.need_mail().digest(hours=max(1, min(hours, 24 * 14)))

    def mail_top(self, limit: int = 10, at_least: str = "important") -> str:
        with self.lock:
            since = datetime.now(UTC) - timedelta(days=7)
            rows = self.need_mail().store.top(
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
            answer = self.need_mail().ask(question, self.llm)
        sources = "\n".join(
            f"- [{h['id']}] {h['at'][:10]} {h['author']}: {h['title']}" for h in answer.sources[:5]
        )
        return answer.text + (f"\n\nSources:\n{sources}" if sources else "")

    # ------------------------------------------------------------------ trading
    def trade_status(self) -> str:
        with self.lock:
            t = self.need_trader()
            account, positions = t.broker.account(), t.broker.positions()
            lines = [
                f"mode {t.broker.mode}; equity {account.equity:,.2f}; cash {account.cash:,.2f}; today {account.day_pnl:+,.2f}",
                f"kill switch {'ON' if Path(self.kill_file).exists() else 'off'}; ledger {'intact' if self.ledger.verify()['ok'] else 'ALTERED'}",
            ]
            lines += [
                f"{p.symbol}: {p.qty:g} shares, average {p.avg_price:,.2f}, now {p.market_price:,.2f}"
                for p in positions
            ]
        return "\n".join(lines)

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

    def trade_place_order(self, symbol: str, side: str, qty: float) -> str:
        with self.lock:
            r = self.need_trader().place_manual(symbol, side.lower(), qty)
        return f"{r.symbol}: {r.status}. {r.detail}"


def build_app(service: Solutions, token: str):
    """The MCP server as an ASGI app (SSE), refusing anything without the bearer token."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("solutions", host="0.0.0.0", port=int(os.environ.get("PORT", "8765")))

    def register(fn):
        async def run(**kwargs: Any) -> str:
            try:
                return await asyncio.to_thread(fn, **kwargs)
            except Exception as error:  # noqa: BLE001 - the model gets the reason, not a stack trace
                return f"Error: {error}"

        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        run.__annotations__ = {k: v for k, v in fn.__annotations__.items()}
        import inspect

        run.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
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
    kill = trader.config.risk.kill_file if trader else ".state/KILL"
    return Solutions(mail, trader, ledger, optional_llm(), kill_file=kill)
