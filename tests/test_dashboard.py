import re

from starlette.testclient import TestClient

from loamz.solutions.concierge import DataConcierge, DataIndex, FolderConnector
from loamz.solutions.concierge.demo import make_demo_folder
from loamz.solutions.dashboard import build_app, curve_svg, render
from loamz.solutions.kit import Ledger
from loamz.solutions.kit.notify import Alert, Once, Quiet
from loamz.solutions.kit.records import Record
from loamz.solutions.mail import MailConcierge, MailStore, Triager
from loamz.solutions.mail.demo import PROFILE, DemoConnector
from loamz.solutions.mail.triage import RuleClassifier
from loamz.solutions.server import Solutions
from loamz.solutions.trading.demo import demo_trader


def service(tmp_path, mail_connector=None):
    mail = MailConcierge(
        mail_connector or DemoConnector(), MailStore(), Triager(RuleClassifier(PROFILE)), Ledger()
    )
    mail.sync()
    trader, ledger = demo_trader(tmp_path / "t")
    trader.place_manual("AAA", "buy", 5)
    trader.snapshot()
    data = DataConcierge(
        DataIndex(), FolderConnector([make_demo_folder(tmp_path / "f")]), mail=mail
    )
    data.scan()
    return Solutions(mail, trader, ledger, data=data, kill_file=str(tmp_path / "t" / "KILL"))


def test_the_page_shows_mail_trading_alerts_and_data(tmp_path):
    alerts = Ledger()
    Once(Quiet(), alerts).send(Alert("a", "Urgent mail: contract", level="urgent"))
    page = render(service(tmp_path), alerts)
    assert "Mail, last 2 days" in page and "Signed contract attached" in page
    assert (
        "kill switch off" in page
        and "ledger intact" in page
        and "AAA" in page
        and "Recent decisions" in page
    )
    assert "Where your data lives" in page


def test_text_from_mail_is_escaped(tmp_path, monkeypatch):
    class Evil:
        name = "evil"

        def fetch(self, since=None, limit=200):
            from datetime import UTC, datetime

            yield Record(
                "e:1",
                "e",
                "email",
                "<script>alert(1)</script>",
                "boss@corp.com",
                datetime.now(UTC),
                "boss@corp.com",
                {
                    "to": ["alex@example.com"],
                    "in_reply_to": "<x>",
                    "from_name": "",
                    "list_unsubscribe": False,
                    "bulk": False,
                    "cc": [],
                },
            )

    from loamz.solutions.mail.demo import PROFILE as P

    monkeypatch.setattr(P, "vips", P.vips | {"boss@corp.com"})
    page = render(service(tmp_path, Evil()))
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_a_token_is_required_off_localhost_and_remembered_in_a_cookie(tmp_path):
    client = TestClient(build_app(service(tmp_path), token="s3"))
    assert client.get("/").status_code == 401 and client.get("/state.json").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
    ok = client.get("/?token=s3")
    assert ok.status_code == 200 and "dashboard" in ok.cookies
    assert client.get("/").status_code == 200  # the cookie
    assert client.get("/state.json").json()["trading"]["orders"] == 1


def test_the_equity_curve_draws_a_line_and_copes_with_no_history():
    assert "<polyline" in curve_svg([100, 101, 99, 103]) and "appears after" in curve_svg([100])
    assert re.search(r'stroke="var\(--up\)"', curve_svg([1, 2])) and "var(--down)" in curve_svg(
        [2, 1]
    )
