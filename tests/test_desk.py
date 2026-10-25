"""The desk: a plan shows every order with its reason, stop and risk and sends nothing; approval sends exactly that plan, sells
first, buys only with cash; fills are recorded at real prices; risk exits never wait for a person; the kill switch stops all."""

from __future__ import annotations

import pytest
from test_lab import make_panel, path

from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.trading.broker import SimBroker
from loamz.solutions.trading.desk import Desk, Plan
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.strategy import MomentumTrend

N = 320


@pytest.fixture
def setup(tmp_path):
    panel = make_panel(
        {
            "SPY": path(N, 0.0005, wiggle=0.002),
            "FAST": path(N, 0.002, wiggle=0.004),
            "SLOW": path(N, 0.0012, wiggle=0.004),
            "DOWN": path(N, -0.0015, wiggle=0.004),
        }
    )
    broker = SimBroker(cash=1_000.0, slippage_bps=0)
    broker.prices = {s: c[-1] for s, c in panel.close.items()} | {"OLD": 100.0}
    broker.holdings = {
        "OLD": (1_000.0, 90.0)
    }  # the book today: one name outside the strategy's universe
    ledger = Ledger()
    desk = Desk(
        broker,
        lambda: panel,
        ledger,
        strategy=MomentumTrend(top=2, keep=3),
        rules=PortfolioRules(idle_in="SPY", max_sector=1.0),
        kill_file=str(tmp_path / "KILL"),
    )
    return desk, broker, panel, tmp_path


def test_a_plan_explains_every_order_and_sends_nothing(setup):
    desk, broker, panel, _ = setup
    plan = desk.plan()
    sides = [(o.side, o.symbol) for o in plan.orders]
    assert sides[0] == ("sell", "OLD") and "not in the strategy's universe" in plan.orders[0].reason
    assert [s for side, s in sides if side == "buy"] == [
        "FAST",
        "SLOW",
        "SPY",
    ]  # the picks, then the idle cash
    fast = next(o for o in plan.orders if o.symbol == "FAST")
    close = panel.close["FAST"][-1]
    assert fast.limit == pytest.approx(close * 1.005, abs=0.01) and fast.stop < close
    assert fast.risk == pytest.approx(fast.qty * (close - fast.stop), rel=0.01)
    assert fast.weight <= 0.15 and "12-1 month" in fast.reason
    assert "stop" in plan.render() and plan.id in plan.render()
    assert broker.orders == {} and Plan.from_json(plan.to_json()) == plan


def test_approval_sends_that_plan_sells_first_and_buys_once_the_cash_is_there(setup):
    desk, broker, _, _ = setup
    plan = desk.plan()
    assert "no proposed plan" in desk.approve("nonsense")
    first = desk.approve(plan.id)
    assert "OLD: sell 1000 limit" in first and "waits for cash" in first
    assert broker.holdings["OLD"][0] == 0 and not broker.holdings.get("FAST")
    later = desk.follow_up()  # the sale has filled: its cash pays for the buys
    assert "OLD: sell 1000 filled at" in later and "FAST: buy" in later
    assert broker.holdings["FAST"][0] > 0 and broker.holdings["SPY"][0] > 0
    assert broker.cash >= 0  # never borrowed
    fills = desk.ledger.entries(kind="fill")
    assert {e.subject for e in fills} == {"order:OLD"}
    desk.follow_up()
    assert {e.subject for e in desk.ledger.entries(kind="fill")} >= {"order:FAST", "order:SPY"}
    assert "already approved" in desk.approve(plan.id)
    assert len(broker.orders) == len(plan.orders)  # nothing was sent twice


def test_a_buy_limit_the_price_never_reaches_is_not_filled(setup):
    desk, broker, _, _ = setup
    broker.holdings = {}
    broker.cash = 100_000.0
    plan = desk.plan()
    broker.prices["FAST"] *= 1.03  # the stock gaps up 3% at the open: the limit is not chased
    desk.approve(plan.id)
    assert not broker.holdings.get("FAST") and broker.holdings["SLOW"][0] > 0
    assert "FAST: buy new" in desk.follow_up()


def test_a_close_below_the_stop_is_sold_without_approval(setup):
    desk, broker, panel, _ = setup
    broker.holdings, broker.cash = {}, 100_000.0
    desk.approve(desk.plan().id)
    stop = __import__("json").loads(desk.ledger.get("desk:stops"))["FAST"]
    panel.close["FAST"][-1] = stop * 0.98
    broker.prices["FAST"] = stop * 0.98
    text = desk.evening(rebalance=False)
    assert "below its stop" in text and "Sent without approval" in text
    assert broker.holdings["FAST"][0] == 0
    assert not [o for o in desk.current().orders if not o.exit]  # an ordinary day: no rebalance


def test_the_kill_switch_stops_the_plan(setup):
    desk, broker, _, tmp = setup
    plan = desk.plan()
    (tmp / "KILL").write_text("stop")
    assert "kill switch" in desk.approve(plan.id)
    assert broker.orders == {}


def test_the_routine_runs_the_desk_instead_of_the_per_symbol_loop(setup):
    from types import SimpleNamespace

    from loamz.solutions.kit.notify import Quiet
    from loamz.solutions.runner import jobs_for
    from loamz.solutions.server import Solutions

    desk = setup[0]
    service = Solutions(None, SimpleNamespace(), None, desk=desk)
    names = [job.name for job in jobs_for(service, Quiet(), {})]
    assert "desk-evening" in names and "desk-follow-up-09:45" in names
    assert "trade" not in names
    plan, ratings = service.trade_plan(), service.trade_ratings("strong buy")
    assert (
        "Plan" in plan.text
        and "Approve button" in plan.text
        and plan.a2ui[0]["createSurface"]["surfaceId"].startswith("plan-")
    )
    assert "rank" in ratings.text and ratings.a2ui
