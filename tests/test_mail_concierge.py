"""Triage rules, the model fallback, the store, the digest and asking: no network, no mailbox."""

from datetime import UTC, datetime, timedelta

import httpx

from loamz.solutions.kit import Ledger
from loamz.solutions.kit.llm import LLM
from loamz.solutions.kit.records import Record
from loamz.solutions.mail import (
    LLMClassifier,
    MailConcierge,
    MailStore,
    Priority,
    Profile,
    RuleClassifier,
    Triager,
)

ME = "me@example.com"
NOW = datetime.now(UTC)


def mail(id, sender, subject, text="", **meta):
    base = {
        "to": [ME],
        "cc": [],
        "in_reply_to": "",
        "list_unsubscribe": False,
        "bulk": False,
        "from_name": "",
    }
    return Record(
        f"t:{id}", "t", "email", subject, text, NOW - timedelta(hours=1), sender, {**base, **meta}
    )


PROFILE = Profile(addresses={ME}, vips={"boss@corp.com", "@bank.com"}, about="I run a small fund.")
RULES = RuleClassifier(PROFILE)


def rank(record):
    return RULES.classify(record)


def test_a_vip_is_important_and_urgent_with_a_deadline():
    assert rank(mail(1, "boss@corp.com", "Lunch?")).priority == Priority.IMPORTANT
    urgent = rank(mail(2, "boss@corp.com", "Numbers", "need the numbers by EOD please"))
    assert urgent.priority == Priority.URGENT and urgent.deadline.lower() == "by eod"
    assert rank(mail(3, "alerts@bank.com", "Hi")).category == "vip"


def test_bulk_mail_is_noise_and_the_unsubscribe_header_changes_the_advice():
    news = rank(mail(4, "news@shop.com", "Big sale", list_unsubscribe=True))
    assert news.priority == Priority.NOISE and news.action == "unsubscribe"
    auto = rank(mail(5, "noreply@service.com", "Your report"))
    assert auto.priority == Priority.NOISE and auto.action == "archive"
    kept = RuleClassifier(Profile(addresses={ME}, never_noise={"news@shop.com"})).classify(
        mail(6, "news@shop.com", "Weekly", list_unsubscribe=True)
    )
    assert kept.priority == Priority.LOW


def test_receipts_are_filed_security_notices_flagged_and_replies_to_you_matter():
    assert rank(mail(7, "orders@store.com", "Your order has shipped")).action == "file"
    security = rank(mail(8, "security@acct.com", "New sign-in to your account"))
    assert security.category == "security" and "confirm" in security.reason
    reply = rank(mail(9, "ann@friend.com", "Re: plan", in_reply_to="<x>"))
    assert reply.priority == Priority.IMPORTANT and reply.category == "conversation"
    assert rank(mail(10, "stranger@x.com", "Hello")) is None  # the rules cannot tell


def llm_answering(content):
    def handle(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return LLM("k", transport=httpx.MockTransport(handle))


def test_undecided_mail_goes_to_the_model_and_bad_answers_are_clamped():
    triager = Triager(
        RULES,
        LLMClassifier(
            llm_answering(
                '{"priority": "IMPORTANT", "category": "Meeting", "action": "bogus", "reason": "an invite", "deadline": ""}'
            ),
            PROFILE,
        ),
    )
    result = triager.triage(mail(11, "stranger@x.com", "Can we meet Friday?"))
    assert (result.priority, result.category, result.action, result.by) == (
        Priority.IMPORTANT,
        "meeting",
        "read",
        "llm",
    )
    assert triager.triage(mail(12, "boss@corp.com", "x")).by == "rules"  # rules never ask the model


def test_without_a_model_undecided_mail_is_normal():
    result = Triager(RULES).triage(mail(13, "stranger@x.com", "Hello"))
    assert (result.priority, result.by) == (Priority.NORMAL, "default")


class Inbox:
    name = "t"

    def __init__(self, records):
        self.records = records

    def fetch(self, since=None, limit=200):
        yield from self.records


def concierge(records, tmp_path, model=None):
    triager = Triager(RULES, LLMClassifier(model, PROFILE) if model else None)
    return MailConcierge(
        Inbox(records), MailStore(tmp_path / "m.sqlite"), triager, Ledger(tmp_path / "l.sqlite")
    )


INBOX = [
    mail(
        20,
        "boss@corp.com",
        "Contract",
        "Priya wants the contract signed by tomorrow",
    ),
    mail(21, "news@shop.com", "Sale!", "50% off", list_unsubscribe=True),
    mail(22, "news@shop.com", "More sale!", "70% off", list_unsubscribe=True),
    mail(23, "orders@store.com", "Your order has shipped", "tracking 123"),
    mail(
        24, "stranger@x.com", "Quarterly numbers", "Priya sent the quarterly numbers for the fund"
    ),
]


def test_sync_triages_each_message_once_and_records_it(tmp_path):
    c = concierge(INBOX, tmp_path)
    first = c.sync()
    assert (first.fetched, first.new, first.triaged) == (5, 5, 5)
    assert first.by_priority == {"urgent": 1, "noise": 2, "low": 1, "normal": 1}
    again = c.sync()
    assert (again.new, again.triaged) == (0, 0)
    kinds = [e.kind for e in c.ledger.entries()]
    assert (
        kinds.count("mail.triaged") == 5
        and kinds.count("mail.synced") == 2
        and c.ledger.verify()["ok"]
    )


def test_the_digest_leads_with_what_needs_you_and_names_noisy_senders(tmp_path):
    c = concierge(INBOX, tmp_path)
    c.sync()
    text = c.digest()
    assert text.index("Needs you today") < text.index("Worth a skim") < text.index("Cleared")
    assert (
        "Contract" in text
        and "boss@corp.com" in text
        and "due: by tomorrow" in text.lower()
        or "by tomorrow" in text
    )
    assert "news@shop.com (2)" in text


def test_asking_searches_the_mail_and_cites_what_it_used(tmp_path):
    c = concierge(INBOX, tmp_path)
    c.sync()
    plain = c.ask("what did Priya say?")
    assert {h["title"] for h in plain.sources} == {"Contract", "Quarterly numbers"}
    answered = c.ask(
        "what did Priya say about the contract?",
        llm_answering("Priya wants it signed by tomorrow [t:20]"),
    )
    assert "signed by tomorrow" in answered.text and answered.sources
    assert c.ask("zebra migration").sources == []


def test_important_mail_is_offered_to_the_sink(tmp_path):
    remembered = []

    class Sink:
        def remember(self, item):
            remembered.append(item)

    c = concierge(INBOX, tmp_path)
    c.sink = Sink()
    c.sync()
    assert [r["subject"] for r in remembered] == ["Contract"]


def test_automated_mail_from_important_senders_is_read_not_replied_to():
    wire = rank(mail(30, "alerts@bank.com", "Wire transfer confirmation"))
    assert (
        wire.priority == Priority.IMPORTANT and wire.action == "read" and "automated" in wire.reason
    )
    person = rank(mail(31, "boss@corp.com", "Quick question"))
    assert person.action == "reply"


def test_meeting_invitations_are_never_buried_as_bulk():
    invite = rank(
        mail(32, "no-reply@calendar.example.com", "Invitation: Board sync, Thu 10:00", bulk=True)
    )
    assert (invite.priority, invite.action, invite.category) == (
        Priority.IMPORTANT,
        "schedule",
        "meeting",
    )
    assert (
        rank(
            mail(33, "calendar-notification@google.com", "Updated invitation: 1:1", bulk=True)
        ).category
        == "meeting"
    )


def test_a_payment_problem_is_urgent_finance_and_not_a_reply():
    problem = rank(
        mail(34, "billing@streamflix.com", "Your payment failed", "We could not charge your card.")
    )
    assert (problem.priority, problem.category, problem.action) == (
        Priority.URGENT,
        "finance",
        "read",
    )


def test_only_newsletters_are_suggested_for_unsubscribing_not_receipts(tmp_path):
    c = concierge(
        [
            mail(40, "news@shop.com", "Sale", list_unsubscribe=True),
            mail(41, "news@shop.com", "Sale 2", list_unsubscribe=True),
            mail(42, "orders@store.com", "Your order has shipped"),
        ],
        tmp_path,
    )
    c.sync()
    text = c.digest()
    assert "news@shop.com (2)" in text and "orders@store.com (" not in text


def test_a_sync_never_makes_more_model_calls_than_its_budget(tmp_path):
    calls = []

    def handle(request):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"priority": "normal", "category": "other", "action": "read", "reason": "x"}'
                        }
                    }
                ]
            },
        )

    model = LLMClassifier(LLM("k", transport=httpx.MockTransport(handle)), PROFILE, max_calls=3)
    records = [mail(100 + n, f"stranger{n}@x.com", f"Hello {n}") for n in range(8)]
    c = MailConcierge(
        Inbox(records),
        MailStore(tmp_path / "b.sqlite"),
        Triager(RULES, model),
        Ledger(tmp_path / "b.sqlite.l"),
    )
    report = c.sync()
    assert len(calls) == 3 and report.triaged == 8 and report.by_priority == {"normal": 8}
    c.store.close()
    c2 = MailConcierge(
        Inbox([mail(200 + n, f"other{n}@x.com", f"Hi {n}") for n in range(2)]),
        MailStore(tmp_path / "c.sqlite"),
        Triager(RULES, model),
        Ledger(tmp_path / "c.sqlite.l"),
    )
    c2.sync()
    assert len(calls) == 5  # the budget starts afresh with each sync
