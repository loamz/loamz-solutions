"""Tell a person: a message to wherever they will see it, once.

    notifier = from_env()                        # NOTIFY_WEBHOOK_URL, NOTIFY_EMAIL_TO + SMTP_*; the console if neither is set
    notifier.send(Alert("mail:42", "Urgent: signed contract", "Priya needs a countersignature by end of day.", "urgent"))

`Once` wraps any notifier so the same alert key is not sent twice within a window (state lives in the ledger, so it survives
restarts). Webhooks speak Slack, Discord and ntfy as well as plain JSON. A channel that fails is logged and skipped: an alert
that cannot be delivered must never stop the work that raised it.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Literal, Protocol

import httpx

from loamz.solutions.kit.ledger import Ledger

logger = logging.getLogger(__name__)
Level = Literal["info", "warn", "urgent"]


@dataclass(frozen=True)
class Alert:
    key: str  # identifies the event: the same key is not announced twice
    title: str
    body: str = ""
    level: Level = "info"


class Notifier(Protocol):
    def send(self, alert: Alert) -> None: ...


class Console:
    def send(self, alert: Alert) -> None:
        print(
            f"[{alert.level.upper()}] {alert.title}" + (f"\n    {alert.body}" if alert.body else "")
        )


class Webhook:
    def __init__(self, url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.url = url
        self.http = httpx.Client(timeout=15, transport=transport)

    def send(self, alert: Alert) -> None:
        text = f"{alert.title}\n{alert.body}".strip()
        if "hooks.slack.com" in self.url:
            response = self.http.post(self.url, json={"text": text})
        elif "discord.com/api/webhooks" in self.url:
            response = self.http.post(self.url, json={"content": text[:1900]})
        elif "ntfy" in self.url:
            priority = {"info": "default", "warn": "high", "urgent": "urgent"}[alert.level]
            response = self.http.post(
                self.url,
                content=alert.body.encode() or b" ",
                headers={"Title": alert.title, "Priority": priority},
            )
        else:
            response = self.http.post(
                self.url,
                json={
                    "key": alert.key,
                    "title": alert.title,
                    "body": alert.body,
                    "level": alert.level,
                },
            )
        response.raise_for_status()


class Email:
    def __init__(self, host: str, user: str, password: str, to: str, *, port: int = 587) -> None:
        self.host, self.user, self.password, self.to, self.port = host, user, password, to, port

    def send(self, alert: Alert) -> None:
        message = EmailMessage()
        message["From"], message["To"] = self.user, self.to
        message["Subject"] = f"[{alert.level}] {alert.title}"
        message.set_content(alert.body or alert.title)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(message)


class Fanout:
    def __init__(self, *channels: Notifier) -> None:
        self.channels = channels

    def send(self, alert: Alert) -> None:
        for channel in self.channels:
            try:
                channel.send(alert)
            except Exception as error:  # noqa: BLE001 - one broken channel must not silence the others
                logger.warning("alert channel %s failed: %s", type(channel).__name__, error)


class Once:
    """Skip an alert whose key was already sent within `window`; the record is in the ledger, so restarts do not repeat it."""

    def __init__(
        self, inner: Notifier, ledger: Ledger, window: timedelta = timedelta(hours=24)
    ) -> None:
        self.inner, self.ledger, self.window = inner, ledger, window

    def send(self, alert: Alert) -> None:
        recent = self.ledger.entries(
            kind="alert.sent", subject=alert.key, since=self.ledger.now() - self.window, limit=1
        )
        if recent:
            return
        self.inner.send(alert)
        self.ledger.append("alert.sent", alert.key, title=alert.title, level=alert.level)


class Quiet:
    """Sends nothing (the default where no one asked to be told)."""

    def send(self, alert: Alert) -> None:
        return None


def from_env(ledger: Ledger | None = None) -> Notifier:
    channels: list[Notifier] = []
    if os.environ.get("NOTIFY_WEBHOOK_URL"):
        channels.append(Webhook(os.environ["NOTIFY_WEBHOOK_URL"]))
    if (
        os.environ.get("NOTIFY_EMAIL_TO")
        and os.environ.get("SMTP_HOST")
        and os.environ.get("MAIL_USER")
        and os.environ.get("MAIL_PASSWORD")
    ):
        channels.append(
            Email(
                os.environ["SMTP_HOST"],
                os.environ["MAIL_USER"],
                os.environ["MAIL_PASSWORD"],
                os.environ["NOTIFY_EMAIL_TO"],
            )
        )
    notifier: Notifier = Fanout(*channels) if channels else Console()
    return Once(notifier, ledger) if ledger else notifier
