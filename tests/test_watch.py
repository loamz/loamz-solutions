from datetime import UTC, datetime, timedelta

import pytest

from loamz.solutions.kit import Ledger
from loamz.solutions.trading.broker import SimBroker
from loamz.solutions.trading.demo import demo_trader
from loamz.solutions.trading.models import Bar, Signal
from loamz.solutions.trading.watch import Watcher, WatchRule, parse_rules

T = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


class Box:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def broker(prev=100.0, now=100.0):
    b = SimBroker(100_000)
    b.history["AAPL"] = [
        Bar(T - timedelta(days=2), prev, prev, prev, prev),
        Bar(T - timedelta(days=1), prev, prev, prev, prev),
    ]
    b.prices["AAPL"] = now
    return b


def test_rules_parse_in_plain_syntax_and_reject_nonsense():
    assert parse_rules("aapl>200, AAPL<170.5 ,TSLA:move=3%, msft:signal") == [
        WatchRule("AAPL", "above", 200),
        WatchRule("AAPL", "below", 170.5),
        WatchRule("TSLA", "move", 3),
        WatchRule("MSFT", "signal"),
    ]
    assert [str(r) for r in parse_rules("AAPL>200,TSLA:move=3%")] == ["AAPL>200", "TSLA:move=3%"]
    with pytest.raises(ValueError, match="Cannot read"):
        parse_rules("AAPL goes up")


def test_levels_fire_only_when_crossed_and_once_a_day():
    box, ledger = Box(), Ledger(clock=lambda: T)
    watcher = Watcher(broker(now=205), parse_rules("AAPL>200,AAPL<170"), box, ledger)
    assert [a.title for a in watcher.run_once()] == ["AAPL is above 200"]
    assert box.sent[0].body == "Last price 205.00." and ledger.entries(kind="watch.triggered")


def test_a_big_move_is_reported_with_its_direction():
    box = Box()
    up = Watcher(broker(prev=100, now=104), parse_rules("AAPL:move=3%"), box, Ledger()).run_once()
    down = Watcher(broker(prev=100, now=96), parse_rules("AAPL:move=3%"), box, Ledger()).run_once()
    quiet = Watcher(
        broker(prev=100, now=101), parse_rules("AAPL:move=3%"), box, Ledger()
    ).run_once()
    assert (
        up[0].title == "AAPL moved +4.0% today"
        and down[0].title == "AAPL moved -4.0% today"
        and quiet == []
    )


class Says:
    def __init__(self, side, confidence):
        self.side, self.confidence = side, confidence

    def predict(self, symbol, bars):
        return Signal(
            symbol, self.side, self.confidence, "momentum turned", bars[-1].ts, bars[-1].close
        )


def test_a_signal_alert_says_whether_the_trader_would_act(tmp_path):
    trader, ledger = demo_trader(tmp_path / "t")
    box = Box()
    watcher = Watcher(
        trader.broker, parse_rules("AAA:signal"), box, ledger, model=Says("buy", 0.9), trader=trader
    )
    alert = watcher.run_once()[0]
    assert alert.title == "Signal: BUY AAA (90%)" and "momentum turned" in alert.body
    assert "Would trade: buy" in alert.body and "every limit passes" in alert.body
    assert trader.broker.positions() == []  # an alert never trades
    assert not [e for e in ledger.entries() if e.kind.startswith("action.")]


def test_a_signal_the_limits_would_stop_says_which_limit(tmp_path):
    trader, ledger = demo_trader(tmp_path / "t")
    (tmp_path / "t" / "KILL").write_text("")
    box = Box()
    Watcher(
        trader.broker, parse_rules("AAA:signal"), box, ledger, model=Says("buy", 0.9), trader=trader
    ).run_once()
    assert "No trade: kill-switch would stop it" in box.sent[0].body


def test_weak_signals_and_holds_raise_nothing(tmp_path):
    trader, ledger = demo_trader(tmp_path / "t")
    for model in (Says("hold", 0.0), Says("buy", 0.3)):
        assert (
            Watcher(
                trader.broker, parse_rules("AAA:signal"), Box(), ledger, model=model, trader=trader
            ).run_once()
            == []
        )
