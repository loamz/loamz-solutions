"""What the trading tools show in the chat: the plan to approve, the account, the ratings, the performance (json-render specs).

Each function takes plain data and returns a spec (`kit.genui`). They draw numbers the desk already computed: the model never
types a figure into a card.
"""

from __future__ import annotations

from typing import Any

from loamz.solutions.kit.genui import money, node, percent, send, spec

RATING_TONE = {
    "strong buy": "success",
    "buy": "info",
    "hold": None,
    "sell": "warning",
    "strong sell": "danger",
}


def row(symbol: str, headline: str, detail: str, *right: Any) -> Any:
    """An initials badge and a name with a line beneath it on the left; whatever `right` holds at the far edge."""
    left = node(
        "Stack",
        node("Avatar", name=symbol, size="md"),
        node(
            "Stack",
            node("Heading", text=headline, level="h4"),
            node("Text", text=detail, variant="caption"),
            direction="vertical",
            gap="none",
        ),
        direction="horizontal",
        gap="md",
        align="center",
    )
    return node(
        "Stack", left, *right, direction="horizontal", gap="md", align="center", justify="between"
    )


def plan_card(plan) -> dict[str, Any]:
    buys = [o for o in plan.orders if o.side == "buy"]
    sells = [o for o in plan.orders if o.side == "sell"]
    at_risk = sum(o.risk or 0 for o in buys)
    blocks = [
        node(
            "Grid",
            node("Metric", label="Paper equity", value=money(plan.equity)),
            node(
                "Metric",
                label="At risk if every stop hits",
                value=money(at_risk),
                detail=percent(at_risk / plan.equity) + " of the account" if plan.equity else None,
                trend="down" if at_risk else None,
            ),
            node(
                "Metric",
                label="Orders",
                value=str(len(plan.orders)),
                detail=f"{len(sells)} sell, {len(buys)} buy",
            ),
            columns=3,
        )
    ]
    if plan.note:
        blocks.append(node("Alert", title="Market regime", message=plan.note, type="warning"))
    for o in plan.orders:
        side_badge = node(
            "Badge", text=o.side.upper(), tone="success" if o.side == "buy" else "danger"
        )
        tags = [side_badge] + ([node("Badge", text="RISK EXIT", tone="warning")] if o.exit else [])
        detail = f"{o.qty:g} shares · limit {o.limit:,.2f} · {money(o.notional)}"
        parts = [
            row(o.symbol, o.symbol, detail, node("Stack", *tags, direction="horizontal", gap="sm")),
            node("Text", text=o.reason, variant="muted"),
        ]
        if o.side == "buy":
            parts.insert(
                1,
                node(
                    "Progress",
                    value=round(o.weight * 100),
                    label="Share of the book after",
                    tone="info",
                ),
            )
        if o.stop and o.risk:
            parts.append(
                node(
                    "Text", text=f"Stop {o.stop:,.2f} · {money(o.risk)} at risk", variant="caption"
                )
            )
        blocks.append(node("Stack", *parts, direction="vertical", gap="sm"))
        blocks.append(node("Separator"))
    if plan.kept:
        blocks.append(
            node("Text", text="Kept as they are: " + ", ".join(plan.kept), variant="muted")
        )
    blocks.append(
        node(
            "Alert",
            title="How it is sent",
            message="Day limit orders at the last close ±0.5%: a stock that gaps past its limit is not bought or sold. Sells go "
            "first; buys go once cash covers them. Risk exits run in the evening routine even without approval.",
            type="info",
        )
    )
    if plan.orders:
        blocks.append(
            node(
                "Stack",
                node(
                    "Button",
                    label=f"Approve plan {plan.id}",
                    variant="primary",
                    on=send(f"Approve plan {plan.id}"),
                ),
                node(
                    "Button",
                    label="Not now",
                    variant="secondary",
                    on=send(f"Don't send plan {plan.id}; keep the book as it is."),
                ),
                direction="horizontal",
                gap="sm",
            )
        )
    return spec(
        node(
            "Card",
            *blocks,
            title=f"Plan {plan.id}",
            description=f"From the {plan.as_of} close · {plan.strategy} · market {plan.regime}",
        )
    )


def account_card(
    mode: str,
    equity: float,
    cash: float,
    day_pnl: float,
    positions,
    kill_switch: bool,
    ledger_ok: bool,
) -> dict[str, Any]:
    rows = []
    for p in sorted(positions, key=lambda p: -p.market_value):
        gain = p.market_price / p.avg_price - 1 if p.avg_price else 0.0
        rows.append(
            row(
                p.symbol,
                p.symbol,
                f"{p.qty:g} shares · average {p.avg_price:,.2f}",
                node(
                    "Stack",
                    node("Heading", text=money(p.market_value), level="h4"),
                    node(
                        "Text",
                        text=f"{percent(gain, signed=True)} · now {p.market_price:,.2f}",
                        variant="caption",
                    ),
                    direction="vertical",
                    gap="none",
                ),
            )
        )
        rows.append(node("Separator"))
    status = []
    if kill_switch:
        status.append(
            node(
                "Alert",
                title="Kill switch is ON",
                message="Nothing will trade until it is resumed.",
                type="error",
            )
        )
    if not ledger_ok:
        status.append(
            node(
                "Alert",
                title="The ledger was altered",
                message="Its hash chain does not verify.",
                type="error",
            )
        )
    return spec(
        node(
            "Card",
            node(
                "Grid",
                node("Metric", label="Equity", value=money(equity)),
                node("Metric", label="Cash", value=money(cash)),
                node(
                    "Metric",
                    label="Today",
                    value=money(day_pnl, signed=True),
                    trend="up" if day_pnl > 0 else "down" if day_pnl < 0 else "neutral",
                ),
                columns=3,
            ),
            *status,
            *(rows or [node("Text", text="No open positions.", variant="muted")]),
            title=f"{mode.capitalize()} account",
            description="Simulated money" if mode == "paper" else None,
        )
    )


def ratings_card(head: str, rows: list[tuple[str, str, str, bool]]) -> dict[str, Any]:
    """rows: (symbol, level, reason, held)."""
    items = []
    for symbol, level, reason, held in rows[:20]:
        tone = RATING_TONE.get(level)
        right = [node("Badge", text=level.upper(), tone=tone)]
        if held:
            right.insert(0, node("Badge", text="HELD"))
        items.append(
            row(symbol, symbol, reason, node("Stack", *right, direction="horizontal", gap="sm"))
        )
        items.append(node("Separator"))
    return spec(node("Card", *items, title="Ratings", description=head))


def performance_card(
    first: str, last: str, account: float, spy: float | None, worst: float, start: float, end: float
) -> dict[str, Any]:
    metrics = [
        node(
            "Metric",
            label="Account",
            value=percent(account, signed=True),
            detail=f"{money(start)} to {money(end)}",
            trend="up" if account >= 0 else "down",
        ),
    ]
    if spy is not None:
        metrics.append(
            node(
                "Metric",
                label="SPY, same days",
                value=percent(spy, signed=True),
                trend="up" if spy >= 0 else "down",
            )
        )
        metrics.append(
            node(
                "Metric",
                label="Account minus SPY",
                value=percent(account - spy, signed=True),
                trend="up" if account >= spy else "down",
            )
        )
    metrics.append(
        node(
            "Metric",
            label="Worst drop from a peak",
            value=percent(worst),
            trend="down" if worst > 0.05 else None,
        )
    )
    return spec(
        node(
            "Card",
            node("Grid", *metrics, columns=2),
            title="Account against SPY",
            description=f"{first} to {last}",
        )
    )
