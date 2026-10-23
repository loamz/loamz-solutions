"""Mail Concierge: see `MailConcierge`."""

from loamz.solutions.mail.concierge import Answer, MailConcierge, SyncReport
from loamz.solutions.mail.imap import ImapConnector, message_to_record
from loamz.solutions.mail.store import MailStore
from loamz.solutions.mail.triage import (
    LLMClassifier,
    Priority,
    Profile,
    RuleClassifier,
    Triage,
    Triager,
)

__all__ = [
    "Answer",
    "ImapConnector",
    "LLMClassifier",
    "MailConcierge",
    "MailStore",
    "Priority",
    "Profile",
    "RuleClassifier",
    "SyncReport",
    "Triage",
    "Triager",
    "message_to_record",
]
