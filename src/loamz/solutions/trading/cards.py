"""What the trading tools show in the chat: the plan to approve, the account, the ratings, the performance (A2UI surfaces).

Each function takes plain data and returns A2UI messages (`kit.a2ui`). They draw numbers the desk already computed: the model
never types a figure into a card.
"""

from __future__ import annotations

from typing import Any

from loamz.solutions.kit.a2ui import (
    avatar,
    badge,
    button,
    callout,
    caption,
    card,
    column,
    divider,
    heading,
    metric,
    metrics,
    money,
    percent,
    progress,
    row,
    send,
    surface,
    text,
)

RATING_TONE = {
    "strong buy": "success",
    "buy": "info",
    "hold": None,
    "sell": "warning",
    "strong sell": "danger",
}


def line(symbol: str, headline: str, detail: str, *right: Any) -> Any:
    """An initials badge and a name with a line beneath it on the left; whatever `right` holds at the far edge."""
    left = row(avatar(symbol), column(heading(headline), caption(detail)))
    return row(left, row(*right), justify="spaceBetween")


def plan_card(plan) -> list[dict[str, Any]]:
    buys = [o for o in plan.orders if o.side == "buy"]
    sells = [o for o in plan.orders if o.side == "sell"]
    at_risk = sum(o.risk or 0 for o in buys)
    blocks: list[Any] = [
        metrics(
            metric("Paper equity", money(plan.equity)),
            metric(
                "At risk if every stop hits",
                money(at_risk),
                detail=percent(at_risk / plan.equity) + " of the account" if plan.equity else None,
                trend="down" if at_risk else None,
            ),
            metric("Orders", str(len(plan.orders)), detail=f"{len(sells)} sell, {len(buys)} buy"),
        )
    ]
    if plan.note:
        blocks.append(callout(plan.note, "Market regime", "warn"))
    for o in plan.orders:
        tags = [badge(o.side.upper(), "success" if o.side == "buy" else "danger")]
        if o.exit:
            tags.append(badge("RISK EXIT", "warn"))
        parts = [
            line(
                o.symbol,
                o.symbol,
                f"{o.qty:g} shares · limit {o.limit:,.2f} · {money(o.notional)}",
                *tags,
            )
        ]
        if o.side == "buy":
            parts.append(progress(round(o.weight * 100), "Share of the book after", "info"))
        parts.append(text(o.reason, "caption"))
        if o.stop and o.risk:
            parts.append(caption(f"Stop {o.stop:,.2f} · {money(o.risk)} at risk"))
        blocks.append(column(*parts))
        blocks.append(divider())
    if plan.kept:
        blocks.append(caption("Kept as they are: " + ", ".join(plan.kept)))
    blocks.append(
        callout(
            "Day limit orders at the last close ±0.5%: a stock that gaps past its limit is not bought or sold. Sells go first; "
            "buys go once cash covers them. Risk exits run in the evening routine even without approval.",
            "How it is sent",
        )
    )
    if plan.orders:
        blocks.append(
            row(
                button(f"Approve plan {plan.id}", send(f"Approve plan {plan.id}"), "primary"),
                button("Not now", send(f"Don't send plan {plan.id}; keep the book as it is.")),
            )
        )
    return surface(
        f"plan-{plan.id}",
        card(
            *blocks,
            title=f"Plan {plan.id}",
            subtitle=f"From the {plan.as_of} close · {plan.strategy} · market {plan.regime}",
        ),
    )


def account_card(
    mode: str,
    equity: float,
    cash: float,
    day_pnl: float,
    positions,
    kill_switch: bool,
    ledger_ok: bool,
) -> list[dict[str, Any]]:
    rows: list[Any] = []
    for p in sorted(positions, key=lambda p: -p.market_value):
        gain = p.market_price / p.avg_price - 1 if p.avg_price else 0.0
        rows.append(
            line(
                p.symbol,
                p.symbol,
                f"{p.qty:g} shares · average {p.avg_price:,.2f}",
                column(
                    heading(money(p.market_value)),
                    caption(f"{percent(gain, signed=True)} · now {p.market_price:,.2f}"),
                    align="end",
                ),
            )
        )
        rows.append(divider())
    status: list[Any] = []
    if kill_switch:
        status.append(
            callout("Nothing will trade until it is resumed.", "Kill switch is ON", "bad")
        )
    if not ledger_ok:
        status.append(callout("Its hash chain does not verify.", "The ledger was altered", "bad"))
    today_trend = "up" if day_pnl > 0 else "down" if day_pnl < 0 else "neutral"
    return surface(
        "account",
        card(
            metrics(
                metric("Equity", money(equity)),
                metric("Cash", money(cash)),
                metric("Today", money(day_pnl, signed=True), trend=today_trend),
            ),
            *status,
            *(rows or [text("No open positions.", "caption")]),
            title=f"{mode.capitalize()} account",
            subtitle="Simulated money" if mode == "paper" else None,
        ),
    )


def ratings_card(head: str, rows: list[tuple[str, str, str, bool]]) -> list[dict[str, Any]]:
    """rows: (symbol, level, reason, held)."""
    items: list[Any] = []
    for symbol, level, reason, held in rows[:20]:
        right = [badge(level.upper(), RATING_TONE.get(level))]
        if held:
            right.insert(0, badge("HELD"))
        items.append(line(symbol, symbol, reason, *right))
        items.append(divider())
    return surface("ratings", card(*items, title="Ratings", subtitle=head))


def performance_card(
    first: str, last: str, account: float, spy: float | None, worst: float, start: float, end: float
) -> list[dict[str, Any]]:
    items = [
        metric(
            "Account",
            percent(account, signed=True),
            detail=f"{money(start)} to {money(end)}",
            trend="up" if account >= 0 else "down",
        )
    ]
    if spy is not None:
        items.append(
            metric("SPY, same days", percent(spy, signed=True), trend="up" if spy >= 0 else "down")
        )
        items.append(
            metric(
                "Account minus SPY",
                percent(account - spy, signed=True),
                trend="up" if account >= spy else "down",
            )
        )
    items.append(
        metric("Worst drop from a peak", percent(worst), trend="down" if worst > 0.05 else None)
    )
    return surface(
        "performance",
        card(metrics(*items), title="Account against SPY", subtitle=f"{first} to {last}"),
    )
