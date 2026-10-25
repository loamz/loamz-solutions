"""The desk: a strategy's target book turned into a plan a person approves, then orders, fills and a record.

    desk = Desk(broker, load_panel, ledger)
    plan = desk.plan()                 # what the strategy wants after the last close: orders, each with its reason, stop and risk
    desk.approve(plan.id)              # a person agreed: the orders go to the broker, sells first, and wait there for the open
    desk.follow_up()                   # fills recorded at their real prices; buys that waited for cash are sent once it is there
    desk.evening()                     # the daily routine: risk exits go at once, a rebalance is proposed on the week's last day

The rules (docs/trading.md):
- a plan is computed from closing prices and stored; approving it sends exactly what was shown, as day limit orders at the
  last close +-0.5% (`band`), so a gap is never chased; an order the price never reached expires at the close;
- a risk exit (a stop, a close below the 100-day average, the drawdown breaker) never waits for a person;
- buys are sent only while settled cash covers them, so the book never borrows; the rest wait for the sells to fill;
- every position gets a stop at entry, `stop_atr` ATRs below the last close; the evening routine exits names that closed under it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from loamz.solutions.kit.genui import Reply
from loamz.solutions.kit.guard import Action, Guard, KillSwitch, RequireApproval
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.notify import Alert, Notifier, Quiet
from loamz.solutions.trading.broker import BrokerError
from loamz.solutions.trading.cards import performance_card, ratings_card
from loamz.solutions.trading.factors import BLEND
from loamz.solutions.trading.panel import Panel, sma
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.risk import AlreadySent, MaxPositionPct, NoShorting
from loamz.solutions.trading.strategy import LEVELS, FactorRank, MomentumTrend, Strategy

STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "momentum-trend": MomentumTrend,
    "blend": lambda: FactorRank(dict(BLEND)),
    "trend": lambda: FactorRank({"trend": 1.0}),
}


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    limit: float
    reason: str
    weight: float  # of equity after the order
    stop: float | None = None  # for a buy: where the position is exited
    risk: float | None = None  # for a buy: dollars lost if the stop is hit
    exit: bool = False  # a risk exit: it runs without approval

    @property
    def notional(self) -> float:
        return self.qty * self.limit


@dataclass
class Plan:
    id: str
    as_of: str  # the close the plan was computed from
    strategy: str
    regime: str
    equity: float
    orders: list[PlannedOrder]
    kept: list[str] = field(default_factory=list)
    note: str = ""

    def render(self) -> str:
        """The plan in words: what the model reads, and what a client without a screen shows."""
        lines = [
            f"Plan {self.id}, from the {self.as_of} close ({self.strategy}; market {self.regime}; paper equity ${self.equity:,.0f})."
        ]
        if self.note:
            lines.append(self.note)
        if not self.orders:
            lines.append("No orders: the book already matches the strategy.")
        for o in self.orders:
            stop = f"; stop {o.stop:,.2f}, ${o.risk:,.0f} at risk" if o.stop and o.risk else ""
            exit_ = " [risk exit: runs without approval]" if o.exit else ""
            lines.append(
                f"- {o.side.upper()} {o.qty:g} {o.symbol} limit {o.limit:,.2f} (~${o.notional:,.0f}, {o.weight:.0%} of the book "
                f"after){stop}. {o.reason}{exit_}"
            )
        if self.kept:
            lines.append("Kept as they are: " + ", ".join(self.kept))
        lines.append(
            "Limits are the last close +-0.5%. Sells go first; buys go once cash covers them."
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(text: str) -> Plan:
        raw = json.loads(text)
        raw["orders"] = [PlannedOrder(**o) for o in raw["orders"]]
        return Plan(**raw)


class Desk:
    def __init__(
        self,
        broker,
        load_panel: Callable[[], Panel],
        ledger: Ledger,
        *,
        strategy: Strategy | None = None,
        rules: PortfolioRules | None = None,
        kill_file: str = ".state/KILL",
        band: float = 0.005,
        drift: float = 0.25,
        exit_below_sma: int = 100,
        notifier: Notifier | None = None,
    ) -> None:
        self.broker, self.load_panel, self.ledger = broker, load_panel, ledger
        self.strategy = strategy or MomentumTrend()
        self.rules = rules or PortfolioRules(idle_in="SPY")
        self.band, self.drift, self.exit_below_sma = band, drift, exit_below_sma
        self.notifier = notifier or Quiet()
        rules_: list = [
            KillSwitch(kill_file),
            NoShorting(),
            MaxPositionPct(self.rules.max_weight + 0.02, exempt={self.rules.idle_in or ""}),
            AlreadySent(),
        ]
        if broker.mode == "live":
            rules_.append(
                RequireApproval(("order",), "live trading: a person must confirm every order")
            )
        self.guard = Guard(
            ledger, rules_
        )  # no approver: a live order is refused here by construction

    # ── reading ──────────────────────────────────────────────────────────────
    def ratings(self, at_least: str = "buy", symbol: str = "") -> Reply:
        panel = self.load_panel()
        t = len(panel.dates) - 1
        held = {p.symbol for p in self.broker.positions()}
        targets = self.strategy.targets(panel, t, held)
        floor = LEVELS.index(at_least) if at_least in LEVELS else LEVELS.index("buy")
        rows = sorted(
            (
                (s, r)
                for s, r in targets.ratings.items()
                if (s == symbol.upper() if symbol else LEVELS.index(r.level) >= floor)
            ),
            key=lambda sr: (-LEVELS.index(sr[1].level), sr[1].rank or 999),
        )
        head = f"From the {panel.dates[t]} close ({self.strategy.name}), market {targets.regime}"
        if not rows:
            return Reply(
                f"Ratings, {head}. "
                + (f"No rating for {symbol.upper()}." if symbol else "Nothing at that level.")
            )
        text = "\n".join(
            [f"Ratings, {head}."]
            + [f"- {s}: {r.level}{' (held)' if s in held else ''}. {r.reason}" for s, r in rows]
        )
        return Reply.shown(
            text, ratings_card(head, [(s, r.level, r.reason, s in held) for s, r in rows])
        )

    # ── planning ─────────────────────────────────────────────────────────────
    def plan(self, rebalance: bool = True) -> Plan:
        """What to do at the next open. `rebalance=False` keeps only the risk exits (the routine on ordinary days)."""
        panel = self.load_panel()
        t = len(panel.dates) - 1
        account = self.broker.account()
        positions = {p.symbol: p for p in self.broker.positions()}
        equity = account.equity
        peak = max(float(self.ledger.get("desk:peak") or 0), equity)
        self.ledger.set("desk:peak", f"{peak:.2f}")
        drawdown = 1 - equity / peak if peak else 0.0
        stops: dict[str, float] = json.loads(self.ledger.get("desk:stops") or "{}")

        def close(symbol: str) -> float | None:
            series = panel.close.get(symbol)
            return series[t] if series and series[t] else None

        def sell_all(symbol: str, reason: str, exit_: bool) -> PlannedOrder:
            price = close(symbol) or positions[symbol].market_price
            return PlannedOrder(
                symbol,
                "sell",
                positions[symbol].qty,
                round(price * (1 - self.band), 2),
                reason,
                0.0,
                exit=exit_,
            )

        orders: dict[str, PlannedOrder] = {}
        # 1. risk exits, every day
        if self.rules.scale(drawdown) == 0:
            for symbol in positions:
                orders[symbol] = sell_all(
                    symbol,
                    f"down {drawdown:.0%} from the peak: the drawdown breaker goes to cash",
                    True,
                )
        for symbol in positions:
            c = close(symbol)
            if symbol in orders or c is None or symbol == self.rules.idle_in:
                continue
            if symbol in stops and c < stops[symbol]:
                orders[symbol] = sell_all(
                    symbol, f"closed at {c:,.2f}, below its stop {stops[symbol]:,.2f}", True
                )
            elif (line := sma(panel.close[symbol], t, self.exit_below_sma)) and c < line:
                orders[symbol] = sell_all(
                    symbol,
                    f"closed below its {self.exit_below_sma}-day average ({line:,.2f})",
                    True,
                )

        targets = self.strategy.targets(panel, t, set(positions))
        kept: list[str] = []
        if rebalance and self.rules.scale(drawdown) > 0:
            names = [s for s in targets.hold if s not in orders]
            weights = self.rules.size(names, panel, t, drawdown=drawdown)
            if targets.regime == "risk on":
                weights = self.rules.park(weights, drawdown)
            for symbol in positions:
                if symbol not in weights and symbol not in orders:
                    rating = targets.ratings.get(symbol)
                    why = (
                        f"left the book: {rating.level}, {rating.reason}"
                        if rating
                        else "not in the strategy's universe"
                    )
                    orders[symbol] = sell_all(symbol, why, False)
            for symbol, w in weights.items():
                c = close(symbol)
                if not c:
                    continue
                held = positions[symbol].qty if symbol in positions else 0.0
                target = math.floor(w * equity / c)
                if held and abs(target - held) <= self.drift * held:
                    kept.append(symbol)
                    continue
                rating = targets.ratings.get(symbol)
                why = (
                    rating.reason
                    if rating
                    else "idle cash, parked in the market while it trends up"
                )
                if target > held:
                    distance = (
                        None
                        if symbol == self.rules.idle_in
                        else self.rules.stop_distance(panel, symbol, t)
                    )
                    stop = round(c - distance, 2) if distance else None
                    qty = target - held
                    orders[symbol] = PlannedOrder(
                        symbol,
                        "buy",
                        qty,
                        round(c * (1 + self.band), 2),
                        ("adds to the position: " if held else "enters: ") + why,
                        round(w, 4),
                        stop,
                        round(qty * distance, 2) if distance else None,
                    )
                elif target < held:
                    orders[symbol] = PlannedOrder(
                        symbol,
                        "sell",
                        held - target,
                        round(c * (1 - self.band), 2),
                        "trimmed to its target size",
                        round(w, 4),
                    )
        idle = self.rules.idle_in
        ordered = sorted(
            orders.values(), key=lambda o: (o.side != "sell", o.symbol == idle, o.symbol)
        )
        as_of = panel.dates[t].isoformat()
        digest = hashlib.sha1(
            json.dumps([as_of, [asdict(o) for o in ordered]], sort_keys=True).encode()
        ).hexdigest()[:8]
        plan = Plan(
            digest,
            as_of,
            self.strategy.name,
            targets.regime,
            round(equity, 2),
            ordered,
            kept,
            targets.note,
        )
        self.ledger.append("plan.proposed", plan.id, plan=json.loads(plan.to_json()))
        self.ledger.set("desk:plan", plan.to_json())
        return plan

    def current(self) -> Plan | None:
        text = self.ledger.get("desk:plan")
        return Plan.from_json(text) if text else None

    # ── acting ───────────────────────────────────────────────────────────────
    def approve(self, plan_id: str) -> str:
        """A person approved the plan they were shown: send it."""
        plan = self.current()
        if plan is None or plan.id != plan_id:
            return f"There is no proposed plan {plan_id}; ask for a fresh plan first."
        if self.ledger.get(f"desk:approved:{plan.id}"):
            return f"Plan {plan.id} was already approved. {self.follow_up()}"
        self.ledger.set(f"desk:approved:{plan.id}", "yes")
        self.ledger.append("plan.approved", plan.id)
        return self.send(plan, plan.orders)

    def send(self, plan: Plan, orders: list[PlannedOrder]) -> str:
        """Sells first; then buys while settled cash covers them (the rest wait for `follow_up`)."""
        lines: list[str] = []
        sent: dict[str, dict] = json.loads(self.ledger.get(f"desk:sent:{plan.id}") or "{}")
        cash = self.broker.account().cash
        stops: dict[str, float] = json.loads(self.ledger.get("desk:stops") or "{}")
        positions = {p.symbol: p for p in self.broker.positions()}
        for o in orders:
            if o.symbol + o.side in sent:
                continue
            if o.side == "buy" and o.notional > cash:
                lines.append(
                    f"- {o.symbol}: buy waits for cash (${o.notional:,.0f} needed, ${max(cash, 0):,.0f} free)"
                )
                continue
            held = positions[o.symbol].qty if o.symbol in positions else 0.0
            params = {
                "side": o.side,
                "qty": o.qty,
                "price": o.limit,
                "notional": round(o.notional, 2),
                "position_pct": o.weight,
                "position_qty": held,
                "plan": plan.id,
                "client_id": f"plan-{plan.id}-{o.symbol}-{o.side}",
                "why": o.reason[:300],
            }
            outcome = self.guard.run(Action("order", o.symbol, params), self.execute(o))
            if outcome.status != "done":
                lines.append(f"- {o.symbol}: {o.side} {outcome.status}: {outcome.reason}")
                continue
            order = outcome.result
            sent[o.symbol + o.side] = {
                "order_id": order["order_id"],
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.qty,
            }
            if o.side == "buy":
                cash -= o.notional
                if o.stop:
                    stops[o.symbol] = o.stop
            elif o.weight == 0:
                stops.pop(o.symbol, None)
            lines.append(
                f"- {o.symbol}: {o.side} {o.qty:g} limit {o.limit:,.2f} sent ({order['status']})"
            )
        self.ledger.set(f"desk:sent:{plan.id}", json.dumps(sent))
        self.ledger.set("desk:stops", json.dumps(stops))
        waiting = sum(1 for line in lines if "waits for cash" in line)
        tail = " Buys that wait for cash go once the sells fill (follow-up)." if waiting else ""
        return (
            f"Plan {plan.id}:\n"
            + "\n".join(lines)
            + (
                "\nOrders sent outside market hours wait for the next open."
                if self.closed()
                else ""
            )
            + tail
        )

    def execute(self, o: PlannedOrder):
        def run(action: Action) -> dict:
            order = self.broker.submit(
                o.symbol, o.side, o.qty, action.params["client_id"], limit=o.limit
            )
            return {
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.qty,
                "limit": o.limit,
                "status": order.status,
                "order_id": order.id,
                "client_id": order.client_id,
                "mode": self.broker.mode,
            }

        return run

    def closed(self) -> bool:
        try:
            return not self.broker.is_open()
        except BrokerError:
            return False

    def follow_up(self) -> str:
        """Record fills at their real prices, and send the buys of the approved plan that were waiting for cash."""
        plan = self.current()
        if plan is None or not self.ledger.get(f"desk:approved:{plan.id}"):
            return "No approved plan to follow up."
        sent: dict[str, dict] = json.loads(self.ledger.get(f"desk:sent:{plan.id}") or "{}")
        recorded = set(json.loads(self.ledger.get(f"desk:filled:{plan.id}") or "[]"))
        lines = []
        for key, info in sent.items():
            try:
                order = self.broker.order(info["order_id"])
            except BrokerError as error:
                lines.append(f"- {info['symbol']}: {error}")
                continue
            if order.status == "filled" and key not in recorded:
                planned = next((o for o in plan.orders if o.symbol + o.side == key), None)
                slip = (
                    (order.filled_avg_price / planned.limit - 1)
                    if planned and order.filled_avg_price
                    else None
                )
                self.ledger.append(
                    "fill",
                    f"order:{info['symbol']}",
                    plan=plan.id,
                    side=order.side,
                    qty=order.filled_qty,
                    price=order.filled_avg_price,
                    limit=planned.limit if planned else None,
                )
                recorded.add(key)
                lines.append(
                    f"- {info['symbol']}: {order.side} {order.filled_qty:g} filled at {order.filled_avg_price:,.2f}"
                    + (f" ({slip:+.2%} against the limit)" if slip is not None else "")
                )
            else:
                lines.append(f"- {info['symbol']}: {order.side} {order.status}")
        self.ledger.set(f"desk:filled:{plan.id}", json.dumps(sorted(recorded)))
        waiting = [o for o in plan.orders if o.symbol + o.side not in sent]
        more = self.send(plan, waiting) if waiting else ""
        return f"Plan {plan.id} orders:\n" + "\n".join(lines) + (f"\n{more}" if more else "")

    # ── the routine ──────────────────────────────────────────────────────────
    def evening(self, rebalance: bool) -> str:
        """After the close: risk exits go out at once; on a rebalance day the full plan is proposed for approval."""
        plan = self.plan(rebalance=rebalance)
        exits = [o for o in plan.orders if o.exit]
        sent = self.send(plan, exits) if exits else ""
        if rebalance and any(not o.exit for o in plan.orders):
            self.notifier.send(
                Alert(
                    f"plan:{plan.id}",
                    "The weekly plan is ready for approval",
                    plan.render(),
                    "info",
                )
            )
        elif exits:
            self.notifier.send(Alert(f"exits:{plan.id}", "Risk exits sent", sent, "warn"))
        return plan.render() + (f"\n\nSent without approval:\n{sent}" if sent else "")

    def performance(self, days: int = 30) -> Reply:
        """The account against SPY over the same closes."""
        history = self.broker.equity_history(days)
        if len(history) < 2:
            return Reply("Not enough account history yet.")
        panel = self.load_panel()
        spy = dict(zip(panel.dates, panel.close.get("SPY") or [], strict=False))
        first, last = history[0], history[-1]
        mine = last[1] / first[1] - 1 if first[1] else 0.0
        a, b = spy.get(first[0].date()), spy.get(last[0].date())
        bench = b / a - 1 if a and b else None
        peak, worst = 0.0, 0.0
        for _, eq in history:
            peak = max(peak, eq)
            worst = max(worst, 1 - eq / peak if peak else 0.0)
        text = (
            f"{first[0].date()} to {last[0].date()}: the account {mine:+.2%} (${first[1]:,.0f} to ${last[1]:,.0f})"
            + (f"; SPY {bench:+.2%}" if bench is not None else "")
            + f"; worst drop from a peak {worst:.1%}."
        )
        card = performance_card(
            str(first[0].date()), str(last[0].date()), mine, bench, worst, first[1], last[1]
        )
        return Reply.shown(text, card)


class PanelSource:
    """Prices from Loamz Data, about two years back, first brought up to the last settled close; reused for `ttl`."""

    def __init__(
        self, data, warehouse, days: int = 750, ttl: timedelta = timedelta(minutes=30)
    ) -> None:
        self.data, self.warehouse, self.days, self.ttl = data, warehouse, days, ttl
        self.cached: Panel | None = None
        self.at: datetime | None = None

    def __call__(self) -> Panel:
        from loamz.solutions.markets.alpaca_data import last_settled_day

        now = datetime.now(UTC)
        if self.cached is not None and self.at and now - self.at < self.ttl:
            return self.cached
        today = last_settled_day(now)
        try:
            self.warehouse.update(self.data, today=today)
        except Exception:  # noqa: BLE001 - stored prices still serve; the plan names the close it used
            pass
        self.cached, self.at = self.warehouse.panel(start=today - timedelta(days=self.days)), now
        return self.cached


def rebalance_day(today: date) -> bool:
    """The weekly rebalance is decided after Friday's close."""
    return today.weekday() == 4
