from datetime import UTC, datetime
from email.message import EmailMessage

from loamz.solutions.mail.imap import ImapConnector, html_to_text, message_to_record


def eml(subject="Hello", body="Hi there", sender="Ann <ann@example.com>", **headers) -> bytes:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, "me@example.com", subject
    message["Date"] = "Thu, 23 Oct 2026 09:30:00 +0000"
    message["Message-ID"] = "<m1@example.com>"
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    message.set_content(body)
    return message.as_bytes()


def test_a_plain_message_becomes_a_record():
    record = message_to_record(eml(), "imap:me", "42")
    assert record.id == "imap:me:42" and record.kind == "email" and record.title == "Hello"
    assert record.author == "ann@example.com" and record.metadata["from_name"] == "Ann"
    assert record.text == "Hi there" and record.at == datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
    assert record.metadata["to"] == ["me@example.com"] and not record.metadata["bulk"]


def test_headers_that_mark_bulk_mail_are_kept():
    record = message_to_record(
        eml(List_Unsubscribe="<mailto:u@x.com>", Precedence="bulk"), "imap:me", "1"
    )
    assert record.metadata["list_unsubscribe"] and record.metadata["bulk"]


def test_html_only_mail_is_reduced_to_text():
    message = EmailMessage()
    message["From"], message["Subject"] = "a@b.com", "Promo"
    message.set_content(
        "<html><head><style>p{}</style></head><body><p>Big <b>sale</b></p><p>today</p></body></html>",
        subtype="html",
    )
    record = message_to_record(message.as_bytes(), "imap:me", "2")
    assert record.text.split() == ["Big", "sale", "today"]
    assert html_to_text("<script>x()</script>a<br>b") == "a\nb"


def test_a_missing_subject_and_date_do_not_break_parsing():
    message = EmailMessage()
    message["From"] = "x@y.com"
    message.set_content("body")
    record = message_to_record(message.as_bytes(), "s", "3")
    assert record.title == "(no subject)" and record.at.tzinfo is not None


class FakeImap:
    """Enough of imaplib for the connector: a mailbox of raw messages by uid."""

    calls: list = []

    def __init__(self, host, port, mailbox=None):
        self.mailbox = (
            mailbox
            if mailbox is not None
            else {b"1": eml("one"), b"2": eml("two"), b"3": eml("three")}
        )

    def login(self, user, password):
        FakeImap.calls.append(("login", user))

    def select(self, folder, readonly=False):
        FakeImap.calls.append(("select", folder, readonly))

    def uid(self, command, *args):
        if command == "SEARCH":
            FakeImap.calls.append(("search", args[1:]))
            return "OK", [b" ".join(self.mailbox)]
        FakeImap.calls.append(("fetch", args[1]))
        return "OK", [(b"1 (BODY[] {n})", self.mailbox[args[0]]), b")"]

    def logout(self):
        FakeImap.calls.append(("logout",))


def test_the_connector_reads_read_only_newest_first_and_logs_out():
    FakeImap.calls = []
    connector = ImapConnector("h", "me@example.com", "pw", factory=FakeImap)
    records = list(connector.fetch(since=datetime(2026, 10, 1, tzinfo=UTC), limit=2))
    assert [r.title for r in records] == ["three", "two"]
    assert ("select", '"INBOX"', True) in FakeImap.calls  # opened read-only
    assert ("search", ("SINCE", "01-Oct-2026")) in FakeImap.calls
    assert all(call[1] == "(BODY.PEEK[])" for call in FakeImap.calls if call[0] == "fetch")
    assert FakeImap.calls[-1] == ("logout",)
