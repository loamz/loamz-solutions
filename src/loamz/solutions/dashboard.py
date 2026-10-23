"""A read-only page that shows the solutions at a glance: mail that needs you, the trader's account and ledger, alerts.

    loamz-solutions dashboard --port 8780        # http://127.0.0.1:8780 ; refreshes itself every minute

It only reads. Bound to 127.0.0.1 it needs no sign-in; on any other address it requires `DASHBOARD_TOKEN` (as `?token=` once, then
a cookie). Everything that came from mail or a model is HTML-escaped.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from loamz.solutions.mail.triage import Priority
from loamz.solutions.server import Solutions
from loamz.solutions.trading.pnl import summarize

STYLE = """
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1a1a1a;--mut:#666;--line:#ddd;--card:#fff;--up:#0a7d3b;--down:#b3261e;--warn:#9a6700}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--line:#2a2a2a;--card:#1a1a1a;--up:#4cc38a;--down:#ff6b6b;--warn:#e3b341}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:20px;margin:8px 0 16px}h2{font-size:15px;margin:0 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:500}
.num{text-align:right;font-variant-numeric:tabular-nums}.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}.mut{color:var(--mut)}
.pill{display:inline-block;padding:0 8px;border-radius:99px;border:1px solid var(--line);font-size:12px}ul{margin:0;padding-left:18px}
"""


def money(value: float, signed: bool = False) -> str:
    return f"{value:+,.2f}" if signed else f"{value:,.2f}"


def sign_class(value: float) -> str:
    return "up" if value > 0 else "down" if value < 0 else "mut"


def curve_svg(points: list[float], width: int = 360, height: int = 80) -> str:
    if len(points) < 2:
        return '<p class="mut">The curve appears after a few passes.</p>'
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    xs = [i * width / (len(points) - 1) for i in range(len(points))]
    ys = [height - 4 - (v - low) / span * (height - 8) for v in points]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
    colour = "var(--up)" if points[-1] >= points[0] else "var(--down)"
    return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="equity curve"><polyline fill="none" stroke="{colour}" stroke-width="2" points="{path}"/></svg>'


def mail_card(service: Solutions) -> str:
    if service.mail is None:
        return '<div class="card"><h2>Mail</h2><p class="mut">Not configured.</p></div>'
    store = service.mail.store
    counts = store.counts(datetime.now(UTC) - timedelta(days=2))
    pills = " ".join(f'<span class="pill">{escape(k)} {n}</span>' for k, n in counts.items())
    rows = store.top(
        since=datetime.now(UTC) - timedelta(days=7), at_least=Priority.IMPORTANT, limit=8
    )
    body = "".join(
        f"<tr><td class='{'down' if r['priority'] == Priority.URGENT else 'warn'}'>{escape(r['priority_name'] or '')}</td>"
        f"<td><b>{escape(r['title'][:80])}</b><br><span class='mut'>{escape(r['author'])}: {escape(r['reason'] or '')}</span></td></tr>"
        for r in rows
    )
    return f'<div class="card"><h2>Mail, last 2 days</h2><p>{pills or "nothing yet"}</p><table>{body or "<tr><td class=mut>Nothing urgent or important.</td></tr>"}</table></div>'


def trading_card(service: Solutions) -> str:
    t = service.trader
    if t is None or service.ledger is None:
        return '<div class="card"><h2>Trading</h2><p class="mut">Not configured.</p></div>'
    account, positions = t.broker.account(), t.broker.positions()
    summary = summarize(service.ledger)
    killed = Path(service.kill_file).exists()
    chain = service.ledger.verify()
    status = (
        f'<span class="pill {"down" if killed else "up"}">kill switch {"ON" if killed else "off"}</span> '
        f'<span class="pill">{escape(t.broker.mode)}</span> '
        f'<span class="pill {"up" if chain["ok"] else "down"}">ledger {"intact" if chain["ok"] else "ALTERED"}</span>'
    )
    rows = "".join(
        f"<tr><td>{escape(p.symbol)}</td><td class=num>{p.qty:g}</td><td class=num>{money(p.avg_price)}</td><td class=num>{money(p.market_price)}</td>"
        f"<td class='num {sign_class(p.market_value - p.qty * p.avg_price)}'>{money(p.market_value - p.qty * p.avg_price, True)}</td></tr>"
        for p in positions
    )
    events = service.ledger.entries(newest_first=True, limit=400)
    interesting = [
        e
        for e in events
        if e.kind
        in ("action.done", "action.denied", "action.refused", "action.failed", "run.error")
    ][:8]
    feed = "".join(
        f"<li>{e.at:%d %b %H:%M} <b>{escape(e.kind.removeprefix('action.'))}</b> {escape(e.subject.removeprefix('order:'))} "
        f"<span class='mut'>{escape(str(e.payload.get('reason') or e.payload.get('error') or (str(e.payload.get('side', '')) + ' ' + str(e.payload.get('qty', '')) + ' @ ' + str(e.payload.get('price', '')))))}</span></li>"
        for e in interesting
    )
    return (
        f'<div class="card"><h2>Trading</h2><p>{status}</p>'
        f"<p>Equity <b>{money(account.equity)}</b> &nbsp; today <b class='{sign_class(account.day_pnl)}'>{money(account.day_pnl, True)}</b> &nbsp; cash {money(account.cash)}</p>"
        f"{curve_svg([v for _, v in summary.curve])}"
        f'<p class="mut">{summary.orders} orders, {summary.round_trips} round trips, win rate {summary.win_rate:.0%}, worst drawdown {summary.max_drawdown_pct:.2%}</p>'
        f"<table><tr><th>Symbol</th><th class=num>Qty</th><th class=num>Avg</th><th class=num>Now</th><th class=num>P&amp;L</th></tr>{rows or '<tr><td colspan=5 class=mut>No positions.</td></tr>'}</table>"
        f'<h2 style="margin-top:12px">Recent decisions</h2><ul>{feed or "<li class=mut>Nothing yet.</li>"}</ul></div>'
    )


def alerts_card(service: Solutions, alerts: Any) -> str:
    rows = "".join(
        f"<li>{e.at:%d %b %H:%M} <span class='{'down' if e.payload.get('level') == 'urgent' else 'warn' if e.payload.get('level') == 'warn' else 'mut'}'>{escape(str(e.payload.get('level')))}</span> {escape(str(e.payload.get('title')))}</li>"
        for e in (alerts.entries(kind="alert.sent", newest_first=True, limit=10) if alerts else [])
    )
    return f'<div class="card"><h2>Recent alerts</h2><ul>{rows or "<li class=mut>None sent.</li>"}</ul></div>'


def data_card(service: Solutions) -> str:
    if service.data is None:
        return '<div class="card"><h2>Data</h2><p class="mut">Not configured.</p></div>'
    return f'<div class="card"><h2>Where your data lives</h2><pre style="white-space:pre-wrap;margin:0;font:inherit">{escape(service.data.inventory())}</pre></div>'


def render(service: Solutions, alerts: Any = None) -> str:
    cards = "".join(
        card(service) if card is not alerts_card else alerts_card(service, alerts)
        for card in (mail_card, trading_card, alerts_card, data_card)
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="60"><title>Loamz solutions</title><style>{STYLE}</style></head><body><main>'
        f'<h1>Loamz solutions <span class="mut" style="font-size:13px">updated {datetime.now():%H:%M:%S}</span></h1><div class="grid">{cards}</div></main></body></html>'
    )


def build_app(service: Solutions, *, token: str = "", alerts: Any = None) -> Starlette:
    def allowed(request: Request) -> bool:
        if not token:
            return True
        given = request.query_params.get("token") or request.cookies.get("dashboard") or ""
        return hmac.compare_digest(given, token)

    async def home(request: Request) -> Response:
        if not allowed(request):
            return Response("Sign-in required: add ?token=…", status_code=401)
        response = HTMLResponse(render(service, alerts))
        if token and request.query_params.get("token"):
            response.set_cookie("dashboard", token, httponly=True, samesite="strict")
        return response

    async def state(request: Request) -> Response:
        if not allowed(request):
            return Response("unauthorized", status_code=401)
        out: dict[str, Any] = {"mail": None, "trading": None}
        if service.trader is not None and service.ledger is not None:
            s = summarize(service.ledger)
            out["trading"] = {
                "equity": s.end_equity,
                "pnl": s.pnl,
                "orders": s.orders,
                "win_rate": s.win_rate,
                "kill_switch": Path(service.kill_file).exists(),
            }
        if service.mail is not None:
            out["mail"] = service.mail.store.counts(datetime.now(UTC) - timedelta(days=2))
        return JSONResponse(out)

    return Starlette(routes=[Route("/", home), Route("/state.json", state)])
