"""Decide how much a message matters, and what to do about it.

Cheap rules answer first and say why (a newsletter header, a person you named as important, a reply to your own message, a
deadline in the text). Only what the rules cannot decide goes to the model, with your profile so it knows what you care
about. With no model configured everything undecided is "normal, read", so the system degrades instead of failing.

Priorities: urgent (today), important (this week), normal, low (skim or file), noise (archive or unsubscribe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol

from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.kit.records import Record


class Priority(IntEnum):
    NOISE = 0
    LOW = 1
    NORMAL = 2
    IMPORTANT = 3
    URGENT = 4

    @classmethod
    def parse(cls, value: str) -> Priority:
        return (
            cls[str(value).strip().upper()]
            if str(value).strip().upper() in cls.__members__
            else cls.NORMAL
        )


ACTIONS = ("reply", "read", "file", "archive", "unsubscribe", "schedule")


@dataclass(frozen=True)
class Triage:
    priority: Priority
    category: str
    action: str
    reason: str
    by: str  # "rules" or "llm" or "default"
    score: int = 0
    deadline: str = ""


@dataclass
class Profile:
    """What matters to you. Everything is optional; the more you say, the better the model ranks."""

    addresses: set[str] = field(default_factory=set)  # your own addresses
    vips: set[str] = field(
        default_factory=set
    )  # senders or whole domains that always matter ("boss@x.com", "@bank.com")
    about: str = ""  # a sentence or two: your role and what you are waiting for
    never_noise: set[str] = field(default_factory=set)  # senders or domains never to bury


URGENT_WORDS = re.compile(
    r"\b(asap|urgent|immediately|by (eod|end of day|tomorrow|tonight|today|noon|cob)|deadline|overdue|final notice|action required|"
    r"payment (failed|declined)|past due|expires? (today|tomorrow))\b",
    re.I,
)
SECURITY_WORDS = re.compile(
    r"\b(security alert|new sign-?in|unusual (sign-?in|activity)|password (reset|changed)|verification code|suspicious)\b",
    re.I,
)
RECEIPT_WORDS = re.compile(
    r"\b(receipt|invoice|order (confirmation|#)|your order|shipped|delivered|payment (received|confirmation)|statement is ready)\b",
    re.I,
)
NOREPLY = re.compile(
    r"(^|[._-])(no-?reply|do-?not-?reply|notifications?|mailer|newsletter|news|updates?)@", re.I
)


def matches(address: str, patterns: set[str]) -> bool:
    address = address.lower()
    return any(
        address == p.lower() or (p.startswith("@") and address.endswith(p.lower()))
        for p in patterns
    )


def direct_to_me(record: Record, profile: Profile) -> bool:
    meta = record.metadata
    return bool(profile.addresses & set(meta.get("to", []))) and not meta.get("list_unsubscribe")


def score_for(priority: Priority, boosts: int = 0) -> int:
    return max(0, min(100, {4: 90, 3: 70, 2: 45, 1: 20, 0: 5}[int(priority)] + boosts))


AUTOMATED = re.compile(
    r"(^|[._-])(no-?reply|do-?not-?reply|notifications?|alerts?|billing|support|security|orders?|mailer|system)@",
    re.I,
)
INVITE_WORDS = re.compile(
    r"^(updated )?(invitation|invite)\b|\binvited you\b|\bhas invited\b|\bmeeting (request|invite)\b",
    re.I,
)
MONEY_WORDS = re.compile(
    r"\b(payment (failed|declined)|past due|overdue|final notice|card (declined|expired)|could not charge)\b",
    re.I,
)


class Classifier(Protocol):
    def classify(self, record: Record) -> Triage | None: ...


def verdict(
    level: Priority, category: str, action: str, reason: str, boost: int = 0, deadline: str = ""
) -> Triage:
    return Triage(level, category, action, reason, "rules", score_for(level, boost), deadline)


class RuleClassifier:
    def __init__(self, profile: Profile | None = None) -> None:
        self.profile = profile or Profile()

    def classify(self, record: Record) -> Triage | None:
        p, meta = self.profile, record.metadata
        sender = record.author
        words = f"{record.title}\n{record.text[:3000]}"
        found = URGENT_WORDS.search(words)
        urgent, deadline = bool(found), (found.group(0) if found else "")
        automated = bool(AUTOMATED.search(sender)) or bool(meta.get("bulk"))
        direct = direct_to_me(record, p) and not meta.get("bulk")

        if matches(sender, p.vips):
            level = Priority.URGENT if urgent else Priority.IMPORTANT
            if automated:
                return verdict(
                    level,
                    "vip",
                    "read",
                    "an automated notice from a sender you marked as important",
                    8,
                    deadline,
                )
            return verdict(
                level, "vip", "reply", "from someone you marked as important", 8, deadline
            )
        if INVITE_WORDS.search(record.title):
            return verdict(
                Priority.IMPORTANT,
                "meeting",
                "schedule",
                "a meeting invitation to accept or decline",
                3,
            )
        if SECURITY_WORDS.search(record.title) and not matches(sender, p.never_noise):
            return verdict(
                Priority.IMPORTANT,
                "security",
                "read",
                "a security notice: confirm it came from the real service before clicking anything",
                5,
            )
        if MONEY_WORDS.search(words) and direct_to_me(record, p):
            return verdict(
                Priority.URGENT,
                "finance",
                "read",
                "a payment or billing problem that can interrupt a service",
            )
        if meta.get("in_reply_to") and direct:
            level = Priority.URGENT if urgent else Priority.IMPORTANT
            return verdict(
                level,
                "conversation",
                "reply",
                "a reply in a conversation addressed to you",
                5,
                deadline,
            )
        if meta.get("list_unsubscribe") or meta.get("bulk") or NOREPLY.search(sender):
            if matches(sender, p.never_noise):
                return verdict(Priority.LOW, "newsletter", "read", "a list you chose to keep")
            action = "unsubscribe" if meta.get("list_unsubscribe") else "archive"
            return verdict(Priority.NOISE, "newsletter", action, "bulk or automated mail")
        if RECEIPT_WORDS.search(record.title):
            return verdict(Priority.LOW, "receipt", "file", "a receipt or order notice")
        if direct and urgent:
            action = "read" if automated else "reply"
            return verdict(
                Priority.URGENT, "request", action, "addressed to you with a deadline", 0, deadline
            )
        return None


SYSTEM = (
    "You triage one email for its owner. Decide how much it matters to them.\n"
    "priority: urgent (needs them today), important (this week), normal, low (skim or file), noise (archive).\n"
    "category: one short word (request, conversation, meeting, finance, security, work, personal, newsletter, social, other).\n"
    f"action: one of {', '.join(ACTIONS)}.\n"
    'Answer {"priority": ..., "category": ..., "action": ..., "reason": "one short sentence", "deadline": "date or empty"}.\n'
    "Be conservative with urgent. Mail from automated systems is never urgent unless money or access is at risk."
)


class LLMClassifier:
    def __init__(self, llm: LLM, profile: Profile | None = None) -> None:
        self.llm, self.profile = llm, profile or Profile()

    def classify(self, record: Record) -> Triage | None:
        about = f"About the owner: {self.profile.about}\n" if self.profile.about else ""
        prompt = (
            f"{about}From: {record.metadata.get('from_name', '')} <{record.author}>\n"
            f"To: {', '.join(record.metadata.get('to', []))}\nSubject: {record.title}\n\n{record.text[:1500]}"
        )
        try:
            answer: dict[str, Any] = self.llm.json(SYSTEM, prompt)
        except LLMError:
            return None
        level = Priority.parse(answer.get("priority", "normal"))
        action = str(answer.get("action", "read")).lower()
        return Triage(
            level,
            str(answer.get("category", "other"))[:30].lower() or "other",
            action if action in ACTIONS else "read",
            str(answer.get("reason", ""))[:300],
            "llm",
            score_for(level),
            str(answer.get("deadline", ""))[:60],
        )


class Triager:
    def __init__(self, rules: Classifier, model: Classifier | None = None) -> None:
        self.rules, self.model = rules, model

    def triage(self, record: Record) -> Triage:
        found = self.rules.classify(record)
        if found:
            return found
        if self.model:
            found = self.model.classify(record)
            if found:
                return found
        return Triage(
            Priority.NORMAL,
            "other",
            "read",
            "no rule matched; add a model for finer ranking",
            "default",
            score_for(Priority.NORMAL),
        )
