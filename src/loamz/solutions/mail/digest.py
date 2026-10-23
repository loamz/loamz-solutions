"""The daily digest: what needs you, in the order it matters, and what you can safely ignore."""

from __future__ import annotations

import smtplib
from collections import Counter
from datetime import datetime
from email.message import EmailMessage
from typing import Any

from loamz.solutions.mail.triage import Priority

ACTION_TEXT = {
    "reply": "reply",
    "read": "read",
    "file": "file it",
    "archive": "archive",
    "unsubscribe": "unsubscribe",
    "schedule": "put on your calendar",
}


def line(item: dict[str, Any]) -> str:
    who = item["author"]
    due = f" (due: {item['deadline']})" if item.get("deadline") else ""
    action = ACTION_TEXT.get(item.get("action") or "", item.get("action") or "")
    return f"- **{item['title'][:90]}** ({who}): {item.get('reason') or ''}{due} → {action}"


def render(
    items: list[dict[str, Any]],
    counts: dict[str, int],
    noisy: list[tuple[str, int]],
    now: datetime,
    *,
    normal_limit: int = 5,
) -> str:
    total = sum(counts.values())
    urgent = [i for i in items if i["priority"] == Priority.URGENT]
    important = [i for i in items if i["priority"] == Priority.IMPORTANT]
    normal = [i for i in items if i["priority"] == Priority.NORMAL][:normal_limit]
    out = [f"# Mail digest, {now:%A %d %B}", ""]
    summary = ", ".join(
        f"{counts[k]} {k}"
        for k in ("urgent", "important", "normal", "low", "noise")
        if counts.get(k)
    )
    out.append(f"{total} new messages: {summary or 'nothing new'}.")
    out.append("")
    if urgent:
        out += ["## Needs you today", *map(line, urgent), ""]
    if important:
        out += ["## This week", *map(line, important), ""]
    if normal:
        out += ["## Worth a skim", *map(line, normal), ""]
    cleared = counts.get("low", 0) + counts.get("noise", 0)
    if cleared:
        out.append(
            f"## Cleared\n{cleared} messages were receipts, newsletters or automated mail. Nothing needs you there."
        )
        if noisy:
            out.append("Most frequent senders you could unsubscribe from or filter:")
            out += [f"- {sender} ({n})" for sender, n in noisy[:5]]
        out.append("")
    if not (urgent or important):
        out += ["Nothing urgent or important. Enjoy the quiet.", ""]
    return "\n".join(out)


def noisy_senders(store, since: datetime | None, limit: int = 5) -> list[tuple[str, int]]:
    """Senders of bulk mail you could unsubscribe from or filter (not receipts: those you may need)."""
    rows = store.top(since=since, at_least=Priority.NOISE, limit=500)
    counter = Counter(
        r["author"]
        for r in rows
        if r["priority"] == Priority.NOISE and r["category"] == "newsletter"
    )
    return counter.most_common(limit)


class SmtpSender:
    def __init__(
        self, host: str, user: str, password: str, *, port: int = 587, to: str | None = None
    ) -> None:
        self.host, self.user, self.password, self.port, self.to = (
            host,
            user,
            password,
            port,
            to or user,
        )

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.user, self.to, subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(message)
