"""A believable synthetic inbox, so you can see the Concierge work before connecting a real mailbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loamz.solutions.kit.records import Record
from loamz.solutions.mail.triage import Profile

ME = "alex@example.com"
PROFILE = Profile(
    addresses={ME},
    vips={"priya@northwind.io", "@examplebank.com"},
    about="I run a small investment fund and a team of five; I am waiting on a signed contract and a wire confirmation.",
)

BASE = {
    "to": [ME],
    "cc": [],
    "in_reply_to": "",
    "list_unsubscribe": False,
    "bulk": False,
    "from_name": "",
}


def inbox(now: datetime | None = None) -> list[Record]:
    now = now or datetime.now(UTC)
    rows = [
        (
            1,
            "priya@northwind.io",
            "Signed contract attached",
            "Hi Alex, the signed contract is attached. Please countersign by end of day tomorrow so legal can file it.",
            0.5,
            {"in_reply_to": "<c1>"},
        ),
        (
            2,
            "alerts@examplebank.com",
            "Wire transfer confirmation",
            "Your wire of $48,000 to Northwind LLC was completed.",
            1,
            {},
        ),
        (
            3,
            "security@accounts-mail.com",
            "New sign-in to your account",
            "A new sign-in from Lisbon was detected. If this was not you, secure your account.",
            2,
            {},
        ),
        (
            4,
            "dana@fundteam.io",
            "Re: Q3 letter draft",
            "Can you review section 3 before Friday? I rewrote the risk paragraph.",
            3,
            {"in_reply_to": "<q3>"},
        ),
        (
            5,
            "mark@recruiting.co",
            "Quick intro?",
            "Hi Alex, I help funds hire analysts. Do you have 15 minutes this week?",
            4,
            {},
        ),
        (
            6,
            "news@marketwatchly.com",
            "Your morning market brief",
            "Stocks futures point higher ahead of earnings...",
            5,
            {"list_unsubscribe": True},
        ),
        (
            7,
            "news@marketwatchly.com",
            "Fed minutes: what to watch",
            "Markets await the minutes...",
            29,
            {"list_unsubscribe": True},
        ),
        (
            8,
            "deals@shopsmart.com",
            "48 hours only: 40% off",
            "Don't miss out on our biggest sale",
            6,
            {"list_unsubscribe": True},
        ),
        (
            9,
            "deals@shopsmart.com",
            "Last chance!",
            "Sale ends tonight",
            30,
            {"list_unsubscribe": True},
        ),
        (
            10,
            "orders@cloudhost.com",
            "Invoice #84921 is ready",
            "Your invoice for October is ready to download.",
            7,
            {},
        ),
        (
            11,
            "no-reply@calendar.example.com",
            "Invitation: Board sync, Thu 10:00",
            "You have been invited to Board sync.",
            8,
            {"bulk": True},
        ),
        (
            12,
            "sam@fundteam.io",
            "Lunch Thursday?",
            "Are you free for lunch Thursday? Thinking the Thai place.",
            9,
            {},
        ),
        (
            13,
            "legal@northwind.io",
            "Question about clause 7",
            "Alex, before you countersign: can we confirm the indemnity cap is $2M? Need your answer asap, we file at 5pm.",
            10,
            {},
        ),
        (
            14,
            "newsletter@devweekly.io",
            "Dev Weekly #412",
            "This week in software...",
            31,
            {"list_unsubscribe": True},
        ),
        (
            15,
            "billing@streamflix.com",
            "Your payment failed",
            "We could not charge your card. Update your payment method to keep your plan.",
            11,
            {},
        ),
    ]
    return [
        Record(
            f"demo:{n}",
            "demo",
            "email",
            subject,
            body,
            now - timedelta(hours=hours),
            sender,
            {**BASE, **meta},
        )
        for n, sender, subject, body, hours, meta in rows
    ]


class DemoConnector:
    name = "demo"

    def fetch(self, since=None, limit=200):
        yield from inbox()[:limit]
