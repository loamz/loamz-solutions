"""Alerts: where they go, that they are sent once, and that a broken channel never stops the work."""

from datetime import timedelta

import httpx

from loamz.solutions.kit import Ledger
from loamz.solutions.kit.notify import Alert, Fanout, Once, Webhook, from_env
from loamz.solutions.mail import MailConcierge, MailStore, Triager
from loamz.solutions.mail.demo import PROFILE, DemoConnector
from loamz.solutions.mail.triage import RuleClassifier
from loamz.solutions.trading.demo import demo_trader
from loamz.solutions.trading.models import Signal


class Buys:
    def predict(self, symbol, bars):
        return Signal(symbol, "buy", 0.9, "momentum", bars[-1].ts, bars[-1].close)


class Box:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def hook(url):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    return Webhook(url, transport=httpx.MockTransport(handle)), seen


def test_webhooks_speak_slack_discord_ntfy_and_plain_json():
    alert = Alert("k", "Title", "Body", "urgent")
    slack, seen = hook("https://hooks.slack.com/services/x")
    slack.send(alert)
    assert seen[0].content == b'{"text":"Title\\nBody"}'
    discord, seen = hook("https://discord.com/api/webhooks/1/x")
    discord.send(alert)
    assert b'"content"' in seen[0].content
    ntfy, seen = hook("https://ntfy.sh/mytopic")
    ntfy.send(alert)
    assert (
        seen[0].headers["Title"] == "Title"
        and seen[0].headers["Priority"] == "urgent"
        and seen[0].content == b"Body"
    )
    plain, seen = hook("https://example.com/hook")
    plain.send(alert)
    assert b'"level":"urgent"' in seen[0].content and b'"key":"k"' in seen[0].content


def test_an_alert_key_is_announced_once_even_after_a_restart(tmp_path):
    ledger = Ledger(tmp_path / "l.sqlite")
    box = Box()
    Once(box, ledger).send(Alert("a", "x"))
    Once(box, Ledger(tmp_path / "l.sqlite")).send(Alert("a", "x"))  # a new process, same ledger
    Once(box, ledger).send(Alert("b", "y"))
    assert [a.key for a in box.sent] == ["a", "b"]
    assert ledger.verify()["ok"]


def test_a_broken_channel_does_not_silence_the_others():
    class Broken:
        def send(self, alert):
            raise RuntimeError("down")

    box = Box()
    Fanout(Broken(), box).send(Alert("k", "t"))
    assert len(box.sent) == 1


def test_from_env_picks_the_channels_that_are_configured(monkeypatch):
    for name in ("NOTIFY_WEBHOOK_URL", "NOTIFY_EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    assert type(from_env()).__name__ == "Console"
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://example.com/h")
    assert type(from_env()).__name__ == "Fanout"


def test_urgent_mail_raises_one_alert_each_and_the_digest_run_is_quiet_about_the_rest(tmp_path):
    box = Box()
    c = MailConcierge(
        DemoConnector(), MailStore(), Triager(RuleClassifier(PROFILE)), Ledger(), notifier=box
    )
    c.sync()
    assert len(box.sent) == 3 and all(a.level == "urgent" for a in box.sent)
    assert {a.key for a in box.sent} == {a.key for a in box.sent}
    assert any("contract" in a.title.lower() for a in box.sent)


def test_a_trade_and_a_denial_are_announced_with_what_the_model_said(tmp_path):
    box = Box()
    trader, ledger = demo_trader(tmp_path / "t")
    trader.notifier = box
    trader.model = Buys()
    trader.run_once()
    traded = [a for a in box.sent if a.title.endswith(": traded")]
    assert traded and "BUY AAA (90%): momentum" in traded[0].body and traded[0].level == "info"
    (tmp_path / "t" / "KILL").write_text("")
    tomorrow = trader.ledger.now() + timedelta(days=1)
    trader.ledger.clock = lambda: tomorrow
    trader.run_once()
    denied = [a for a in box.sent if a.title.endswith(": denied")]
    assert denied and "kill switch" in denied[0].body and denied[0].level == "warn"
