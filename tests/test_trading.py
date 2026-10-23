"""The trading loop with a simulated broker: sizing, every risk rule, idempotency, the ledger and the P&L."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from loamz.solutions.kit import Ledger
from loamz.solutions.trading.backtest import backtest, load_csv, synth_bars
from loamz.solutions.trading.broker import SimBroker
from loamz.solutions.trading.engine import Trader, TraderConfig, order_id
from loamz.solutions.trading.models import Bar, Signal
from loamz.solutions.trading.pnl import summarize
from loamz.solutions.trading.risk import RiskConfig
from loamz.solutions.trading.signals import HttpSignalModel, MovingAverageCross

T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


class Scripted:
    """A model that answers from a table: symbol -> (side, confidence)."""

    def __init__(self, answers):
        self.answers = answers

    def predict(self, symbol, bars):
        side, confidence = self.answers.get(symbol, ("hold", 0))
        return Signal(
            symbol, side, confidence, f"scripted {side}", T0, bars[-1].close if bars else None
        )


def setup(tmp_path, answers, *, cash=100_000.0, symbols=("AAPL",), **risk):
    broker = SimBroker(cash, slippage_bps=0)
    for s in symbols:
        broker.prices[s] = 100.0
        broker.history[s] = [Bar(T0, 100, 100, 100, 100)]
    clock = [T0]
    ledger = Ledger(tmp_path / "t.sqlite", clock=lambda: clock[0])
    config = TraderConfig(
        RiskConfig(set(symbols), kill_file=str(tmp_path / "KILL"), **risk),
        min_confidence=0.6,
        risk_per_trade=0.01,
    )
    return Trader(broker, Scripted(answers), config, ledger), broker, ledger, clock


def test_a_confident_buy_is_sized_by_risk_and_confidence(tmp_path):
    trader, broker, ledger, _ = setup(tmp_path, {"AAPL": ("buy", 0.8)})
    report = trader.run_once()
    assert report.results[0].status == "traded"
    # 1% of 100,000 x 0.8 = 800 of risk, at 100 a share
    assert broker.positions()[0].qty == 8
    kinds = [e.kind for e in ledger.entries()]
    assert (
        kinds[:4] == ["signal", "action.proposed", "action.done", "equity"]
        and ledger.verify()["ok"]
    )


def test_hold_and_low_confidence_do_nothing_and_say_why(tmp_path):
    trader, broker, ledger, _ = setup(tmp_path, {"AAPL": ("buy", 0.4)})
    result = trader.run_once().results[0]
    assert result.status == "no trade" and "below 60%" in result.detail and broker.positions() == []
    assert ledger.entries(kind="no.trade")


def test_selling_closes_the_position_and_never_goes_short(tmp_path):
    trader, broker, _, clock = setup(tmp_path, {"AAPL": ("sell", 0.9)})
    assert "nothing to sell" in trader.run_once().results[0].detail
    broker.submit("AAPL", "buy", 10, "seed")
    clock[0] += timedelta(days=1)
    result = trader.run_once().results[0]
    assert result.status == "traded" and broker.positions() == []


def test_the_notional_and_position_limits_hold(tmp_path):
    trader, broker, ledger, _ = setup(tmp_path, {"AAPL": ("buy", 1.0)}, max_order_notional=500)
    result = trader.run_once().results[0]
    assert (
        result.status == "denied" and "max-notional" in result.detail and broker.positions() == []
    )
    capped, broker2, _, _ = setup(tmp_path / "b", {"AAPL": ("buy", 1.0)}, max_position_pct=0.0005)
    assert "position limit" in capped.run_once().results[0].detail and broker2.positions() == []


def test_a_symbol_off_the_allowlist_is_never_traded(tmp_path):
    trader, broker, _, _ = setup(tmp_path, {"AAPL": ("buy", 1.0)}, symbols=("AAPL",))
    trader.config.risk.symbols.add(
        "TSLA"
    )  # configured, then removed from the guard's list to prove the rule
    trader.guard.rules[1].allowed.discard("TSLA")
    broker.prices["TSLA"] = 100.0
    broker.history["TSLA"] = [Bar(T0, 100, 100, 100, 100)]
    trader.model.answers["TSLA"] = ("buy", 1.0)
    statuses = {r.symbol: r.status for r in trader.run_once().results}
    assert statuses == {"AAPL": "traded", "TSLA": "denied"}


def test_the_daily_loss_limit_stops_buying_but_not_selling(tmp_path):
    trader, broker, _, clock = setup(tmp_path, {"AAPL": ("buy", 1.0)}, max_daily_loss_pct=0.02)
    broker.last_equity = 110_000  # down about 9% on the day
    assert "daily loss limit" in trader.run_once().results[0].detail
    broker.submit("AAPL", "buy", 5, "seed")
    trader.model.answers["AAPL"] = ("sell", 1.0)
    clock[0] += timedelta(days=1)
    assert trader.run_once().results[0].status == "traded"


def test_one_order_per_symbol_side_and_day_and_a_cooldown(tmp_path):
    trader, broker, ledger, clock = setup(tmp_path, {"AAPL": ("buy", 1.0)}, cooldown_minutes=30)
    assert trader.run_once().results[0].status == "traded"
    again = trader.run_once().results[0]  # same moment: the cooldown refuses
    assert again.status == "denied" and "cooldown" in again.detail
    assert order_id("AAPL", "buy", "2026-10-23") == order_id("AAPL", "buy", "2026-10-23")
    assert order_id("AAPL", "buy", "2026-10-23") != order_id("AAPL", "sell", "2026-10-23")
    assert broker.positions()[0].qty == 10


def test_the_order_count_limit_and_the_kill_switch(tmp_path):
    trader, broker, ledger, clock = setup(
        tmp_path, {"AAPL": ("buy", 1.0)}, symbols=("AAPL", "MSFT"), max_orders_per_day=1
    )
    trader.model.answers["MSFT"] = ("buy", 1.0)
    results = {r.symbol: r for r in trader.run_once().results}
    assert results["AAPL"].status == "traded" and "orders already today" in results["MSFT"].detail
    (tmp_path / "KILL").write_text("")
    clock[0] += timedelta(days=1)
    stopped = trader.run_once().results[0]
    assert stopped.status == "denied" and "kill switch" in stopped.detail


class Closed(SimBroker):
    mode = "paper"

    def __init__(self):
        super().__init__(100_000)
        self.open = False


def test_a_paper_account_does_not_trade_when_the_market_is_closed(tmp_path):
    broker = Closed()
    broker.prices["AAPL"] = 100.0
    broker.history["AAPL"] = [Bar(T0, 100, 100, 100, 100)]
    config = TraderConfig(RiskConfig({"AAPL"}, kill_file=str(tmp_path / "KILL")))
    trader = Trader(broker, Scripted({"AAPL": ("buy", 1.0)}), config, Ledger(":memory:"))
    result = trader.run_once().results[0]
    assert result.status == "denied" and "closed" in result.detail


def test_a_live_account_needs_a_person_to_approve_each_order(tmp_path):
    class Live(SimBroker):
        mode = "live"

    broker = Live(100_000)
    broker.prices["AAPL"] = 100.0
    broker.history["AAPL"] = [Bar(T0, 100, 100, 100, 100)]
    config = TraderConfig(RiskConfig({"AAPL"}, kill_file=str(tmp_path / "KILL")))
    refused = Trader(broker, Scripted({"AAPL": ("buy", 1.0)}), config, Ledger(":memory:"))
    assert refused.run_once().results[0].status == "refused"
    asked = []
    approved = Trader(
        broker,
        Scripted({"AAPL": ("buy", 1.0)}),
        config,
        Ledger(":memory:"),
        approver=lambda a, why: asked.append(why) or True,
    )
    assert approved.run_once().results[0].status == "traded" and "live trading" in asked[0]


def test_pnl_is_computed_from_the_ledger_alone(tmp_path):
    trader, broker, ledger, clock = setup(tmp_path, {"AAPL": ("buy", 1.0)})
    trader.run_once()
    broker.prices["AAPL"] = 110.0
    clock[0] += timedelta(days=1)
    trader.model.answers["AAPL"] = ("sell", 1.0)
    trader.run_once()
    s = summarize(ledger)
    assert (s.orders, s.round_trips, s.wins) == (2, 1, 1)
    assert s.realized == pytest.approx(100.0)  # 10 shares, 100 -> 110
    assert s.end_equity == pytest.approx(100_100.0) and s.pnl_pct == pytest.approx(0.001)
    assert "win rate 100%" in s.render()


def test_the_moving_average_baseline_needs_history_and_finds_a_crossover():
    model = MovingAverageCross(3, 6)
    flat = [Bar(T0 + timedelta(days=i), 10, 10, 10, 10) for i in range(10)]
    assert model.predict("X", flat[:3]).side == "hold" and model.predict("X", flat).side == "hold"
    rising = flat[:7] + [
        Bar(T0 + timedelta(days=7 + i), 12 + i, 12 + i, 12 + i, 12 + i) for i in range(3)
    ]
    sides = {model.predict("X", rising[: n + 1]).side for n in range(7, 10)}
    assert "buy" in sides
    with pytest.raises(ValueError):
        MovingAverageCross(10, 5)


def test_a_model_you_serve_is_used_and_a_failing_one_means_hold():
    bars = [Bar(T0, 1, 1, 1, 1)]

    def answer(payload):
        return HttpSignalModel(
            "http://m", "tok", transport=httpx.MockTransport(lambda r: payload(r))
        )

    ok = answer(
        lambda r: httpx.Response(200, json={"side": "buy", "confidence": 0.9, "rationale": "alpha"})
    )
    signal = ok.predict("X", bars)
    assert (signal.side, signal.confidence, signal.rationale) == ("buy", 0.9, "alpha")
    for bad in (
        httpx.Response(500),
        httpx.Response(200, json={"side": "moon", "confidence": 1}),
        httpx.Response(200, json={"side": "buy", "confidence": 7}),
        httpx.Response(200, text="not json"),
    ):
        failed = answer(lambda r, bad=bad: bad).predict("X", bars)
        assert failed.side == "hold" and "staying out" in failed.rationale


def test_a_backtest_fills_at_the_next_open_not_the_signal_price():
    t = datetime(2024, 1, 2, tzinfo=UTC)
    bars = [Bar(t + timedelta(days=i), 100, 100, 100, 100) for i in range(40)]
    bars[36] = Bar(t + timedelta(days=36), 100, 100, 100, 100)
    bars[37] = Bar(
        t + timedelta(days=37), 130, 130, 130, 130
    )  # the open after the signal day jumps
    scripted = Scripted({})

    class BuyOnce:
        done = False

        def predict(self, symbol, history):
            if not self.done and len(history) == 37:
                self.done = True
                return Signal(symbol, "buy", 1.0, "go", history[-1].ts, history[-1].close)
            return Signal(symbol, "hold", 0, "", history[-1].ts, history[-1].close)

    config = TraderConfig(
        RiskConfig({"X"}, max_order_notional=1e9, max_position_pct=1.0), risk_per_trade=0.5
    )
    result = backtest(BuyOnce(), {"X": bars}, config, warmup=35, slippage_bps=0)
    assert scripted and result.trades[0].payload["price"] == pytest.approx(130.0)  # not 100


def test_a_full_backtest_runs_deterministically_and_reports(tmp_path):
    bars = {s: synth_bars(400, seed) for s, seed in (("AAA", 1), ("BBB", 2))}
    config = TraderConfig(RiskConfig({"AAA", "BBB"}, max_orders_per_day=20, cooldown_minutes=0))
    first = backtest(MovingAverageCross(10, 30), bars, config)
    second = backtest(MovingAverageCross(10, 30), bars, config)
    assert first.summary.orders > 3 and first.summary.end_equity == second.summary.end_equity
    assert first.ledger.verify()["ok"] and "drawdown" in first.summary.render()
    path = tmp_path / "bars.csv"
    path.write_text(
        "date,open,high,low,close,volume\n"
        + "\n".join(
            f"{b.ts:%Y-%m-%d},{b.open},{b.high},{b.low},{b.close},{b.volume}"
            for b in bars["AAA"][:50]
        )
    )
    assert len(load_csv(str(path))) == 50
